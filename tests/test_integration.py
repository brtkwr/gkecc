"""Integration tests with mocked external dependencies"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch
from datetime import datetime
import pytest

from main import (
    get_cache_dir,
    load_sku_cache,
    save_sku_cache,
    parse_pricing_data,
    generate_compute_class,
)


class TestCacheManagement:
    """Tests for cache management functions"""

    def test_get_cache_dir(self, mocker):
        """Test cache directory is created"""
        mock_home = Path("/fake/home")
        mocker.patch("pathlib.Path.home", return_value=mock_home)
        mock_mkdir = mocker.patch("pathlib.Path.mkdir")

        cache_dir = get_cache_dir()

        assert cache_dir == mock_home / ".cache" / "gkecc"
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    def test_load_sku_cache_not_exists(self, mocker):
        """Test loading cache when file doesn't exist"""
        mocker.patch("main.get_cache_dir", return_value=Path("/fake/cache"))
        mocker.patch("pathlib.Path.exists", return_value=False)

        result = load_sku_cache()

        assert result is None

    def test_load_sku_cache_fresh(self, mocker):
        """Test loading fresh cache from today"""
        today = datetime.now().strftime("%Y-%m-%d")
        cache_data = {
            "date": today,
            "skus": [{"description": "Test SKU", "price": 0.01}],
        }

        mocker.patch("main.get_cache_dir", return_value=Path("/fake/cache"))
        mocker.patch("pathlib.Path.exists", return_value=True)
        mock_file = mocker.mock_open(read_data=json.dumps(cache_data))
        mocker.patch("builtins.open", mock_file)

        result = load_sku_cache()

        assert result == cache_data["skus"]

    def test_load_sku_cache_stale(self, mocker):
        """Test loading stale cache from yesterday"""
        cache_data = {
            "date": "2020-01-01",
            "skus": [{"description": "Test SKU", "price": 0.01}],
        }

        mocker.patch("main.get_cache_dir", return_value=Path("/fake/cache"))
        mocker.patch("pathlib.Path.exists", return_value=True)
        mock_file = mocker.mock_open(read_data=json.dumps(cache_data))
        mocker.patch("builtins.open", mock_file)

        result = load_sku_cache()

        assert result is None

    def test_load_sku_cache_corrupted(self, mocker):
        """Test loading corrupted cache file"""
        mocker.patch("main.get_cache_dir", return_value=Path("/fake/cache"))
        mocker.patch("pathlib.Path.exists", return_value=True)
        mock_file = mocker.mock_open(read_data="invalid json")
        mocker.patch("builtins.open", mock_file)

        result = load_sku_cache()

        assert result is None

    def test_save_sku_cache(self, mocker):
        """Test saving SKU data to cache"""
        sku_data = [{"description": "Test SKU", "price": 0.01}]
        today = datetime.now().strftime("%Y-%m-%d")

        mocker.patch("main.get_cache_dir", return_value=Path("/fake/cache"))
        mock_file = mocker.mock_open()
        mocker.patch("builtins.open", mock_file)
        mock_json_dump = mocker.patch("json.dump")

        save_sku_cache(sku_data)

        mock_file.assert_called_once()
        mock_json_dump.assert_called_once()
        call_args = mock_json_dump.call_args[0][0]
        assert call_args["date"] == today
        assert call_args["skus"] == sku_data

    def test_save_sku_cache_error(self, mocker):
        """Test handling error when saving cache"""
        sku_data = [{"description": "Test SKU", "price": 0.01}]

        mocker.patch("main.get_cache_dir", return_value=Path("/fake/cache"))
        mocker.patch("builtins.open", side_effect=OSError("Permission denied"))

        # Should not raise exception
        save_sku_cache(sku_data)


class TestParsePricingData:
    """Tests for parse_pricing_data function"""

    def create_mock_sku(self, description, regions, price):
        """Helper to create a mock SKU"""
        sku = MagicMock()
        sku.description = description
        sku.service_regions = regions

        # Mock pricing info
        pricing_info = MagicMock()
        tier = MagicMock()
        unit_price = MagicMock()
        unit_price.units = int(price)
        unit_price.nanos = int((price - int(price)) * 1e9)
        tier.unit_price = unit_price
        pricing_info.pricing_expression.tiered_rates = [tier]
        sku.pricing_info = [pricing_info]

        return sku

    def test_parse_pricing_data_with_cache(self, mocker):
        """Test parsing pricing data using cached data"""
        cached_data = {
            "t2d": {
                "spot_core": 0.00313,
                "spot_ram": 0.00042,
                "ondemand_core": 0.0157,
                "ondemand_ram": 0.0021,
            }
        }

        mocker.patch("main.load_sku_cache", return_value=[
            {
                "description": "Spot Preemptible T2D Instance Core running in Europe",
                "regions": ["europe-north1"],
                "price": 0.00313,
            },
            {
                "description": "Spot Preemptible T2D Instance Ram running in Europe",
                "regions": ["europe-north1"],
                "price": 0.00042,
            },
            {
                "description": "T2D Instance Core running in Europe",
                "regions": ["europe-north1"],
                "price": 0.0157,
            },
            {
                "description": "T2D Instance Ram running in Europe",
                "regions": ["europe-north1"],
                "price": 0.0021,
            },
        ])

        result = parse_pricing_data(region="europe-north1", arch="amd64", use_cache=True)

        assert "t2d" in result
        assert result["t2d"]["spot_core"] == 0.00313
        assert result["t2d"]["spot_ram"] == 0.00042
        assert result["t2d"]["ondemand_core"] == 0.0157
        assert result["t2d"]["ondemand_ram"] == 0.0021

    def test_parse_pricing_data_no_cache(self, mocker):
        """Test parsing pricing data from API"""
        mocker.patch("main.load_sku_cache", return_value=None)

        # Mock GCP client
        mock_client = MagicMock()
        mock_service = MagicMock()
        mock_service.name = "services/compute-engine"
        mock_service.display_name = "Compute Engine"

        mock_client.list_services.return_value = [mock_service]
        mock_client.list_skus.return_value = [
            self.create_mock_sku(
                "Spot Preemptible N2D Instance Core running in Europe",
                ["europe-north1"],
                0.00552,
            ),
            self.create_mock_sku(
                "Spot Preemptible N2D Instance Ram running in Europe",
                ["europe-north1"],
                0.00077,
            ),
            self.create_mock_sku(
                "N2D Instance Core running in Europe",
                ["europe-north1"],
                0.0276,
            ),
            self.create_mock_sku(
                "N2D Instance Ram running in Europe",
                ["europe-north1"],
                0.00385,
            ),
        ]

        mocker.patch("main.billing_v1.CloudCatalogClient", return_value=mock_client)
        mocker.patch("main.save_sku_cache")

        result = parse_pricing_data(region="europe-north1", arch="amd64", use_cache=False)

        assert "n2d" in result
        assert result["n2d"]["spot_core"] == 0.00552
        assert result["n2d"]["spot_ram"] == 0.00077

    def test_parse_pricing_data_filter_arm(self, mocker):
        """Test filtering ARM instances"""
        mocker.patch("main.load_sku_cache", return_value=[
            {
                "description": "Spot Preemptible T2A Instance Core running in Europe",
                "regions": ["europe-north1"],
                "price": 0.003,
            },
            {
                "description": "Spot Preemptible T2A Instance Ram running in Europe",
                "regions": ["europe-north1"],
                "price": 0.0004,
            },
            {
                "description": "T2A Instance Core running in Europe",
                "regions": ["europe-north1"],
                "price": 0.015,
            },
            {
                "description": "T2A Instance Ram running in Europe",
                "regions": ["europe-north1"],
                "price": 0.002,
            },
        ])

        result = parse_pricing_data(region="europe-north1", arch="amd64", use_cache=True)

        # t2a is ARM, should be filtered out
        assert "t2a" not in result

    def test_parse_pricing_data_filter_region(self, mocker):
        """Test filtering by region"""
        mocker.patch("main.load_sku_cache", return_value=[
            {
                "description": "Spot Preemptible N2D Instance Core running in US",
                "regions": ["us-central1"],
                "price": 0.005,
            },
        ])

        result = parse_pricing_data(region="europe-north1", arch="amd64", use_cache=True)

        # Different region, should be empty
        assert len(result) == 0


class TestGenerateComputeClassIntegration:
    """Integration tests for generate_compute_class"""

    def test_generate_compute_class_with_output_file(self, mocker, tmp_path):
        """Test generating compute class to a file"""
        mock_pricing = {
            "t2d": {
                "spot_core": 0.00313,
                "spot_ram": 0.00042,
                "ondemand_core": 0.0157,
                "ondemand_ram": 0.0021,
            },
            "n2d": {
                "spot_core": 0.00552,
                "spot_ram": 0.00077,
                "ondemand_core": 0.0276,
                "ondemand_ram": 0.00385,
            },
        }

        mocker.patch("main.parse_pricing_data", return_value=mock_pricing)

        output_file = tmp_path / "test-output.yaml"

        generate_compute_class(
            region="europe-north1",
            output_file=str(output_file),
            vcpus=4,
            ram_gb=16,
            max_daily_cost=None,
            arch="amd64",
            use_cache=True,
            node_labels=None,
        )

        assert output_file.exists()
        content = output_file.read_text()
        assert "apiVersion: cloud.google.com/v1" in content
        assert "kind: ComputeClass" in content
        assert "t2d" in content
        assert "n2d" in content

    def test_generate_compute_class_no_pricing(self, mocker):
        """Test handling when no pricing data is available"""
        mocker.patch("main.parse_pricing_data", return_value=None)

        # Should not raise exception
        generate_compute_class(
            region="europe-north1",
            vcpus=4,
            ram_gb=16,
        )

    def test_generate_compute_class_with_max_cost(self, mocker, tmp_path):
        """Test filtering by max daily cost"""
        mock_pricing = {
            "t2d": {
                "spot_core": 0.00313,
                "spot_ram": 0.00042,
                "ondemand_core": 0.0157,
                "ondemand_ram": 0.0021,
            },
            "n2d": {
                "spot_core": 0.05,  # Very expensive
                "spot_ram": 0.05,
                "ondemand_core": 0.1,
                "ondemand_ram": 0.1,
            },
        }

        mocker.patch("main.parse_pricing_data", return_value=mock_pricing)

        output_file = tmp_path / "test-output.yaml"

        generate_compute_class(
            region="europe-north1",
            output_file=str(output_file),
            vcpus=4,
            ram_gb=16,
            max_daily_cost=1.0,  # Very low limit
            arch="amd64",
            use_cache=True,
            node_labels=None,
        )

        content = output_file.read_text()
        assert "t2d" in content
        # n2d should be filtered out due to high cost
        assert "n2d" not in content

    def test_generate_compute_class_with_node_labels(self, mocker, tmp_path):
        """Test generating with node labels"""
        mock_pricing = {
            "t2d": {
                "spot_core": 0.00313,
                "spot_ram": 0.00042,
                "ondemand_core": 0.0157,
                "ondemand_ram": 0.0021,
            },
        }

        mocker.patch("main.parse_pricing_data", return_value=mock_pricing)

        output_file = tmp_path / "test-output.yaml"

        generate_compute_class(
            region="europe-north1",
            output_file=str(output_file),
            vcpus=4,
            ram_gb=16,
            max_daily_cost=None,
            arch="amd64",
            use_cache=True,
            node_labels={"env": "production", "team": "platform"},
        )

        content = output_file.read_text()
        assert "nodeLabels:" in content
        assert 'env: "production"' in content
        assert 'team: "platform"' in content

    def test_generate_compute_class_table_format(self, mocker, capsys):
        """Test generating table format output"""
        mock_pricing = {
            "t2d": {
                "spot_core": 0.00313,
                "spot_ram": 0.00042,
                "ondemand_core": 0.0157,
                "ondemand_ram": 0.0021,
            },
        }

        mocker.patch("main.parse_pricing_data", return_value=mock_pricing)
        mocker.patch("main.FORMAT", "table")

        generate_compute_class(
            region="europe-north1",
            vcpus=4,
            ram_gb=16,
        )

        captured = capsys.readouterr()
        assert "Family" in captured.out
        assert "Type" in captured.out
        assert "Daily Cost" in captured.out
        assert "t2d" in captured.out

    def test_generate_compute_class_all_filtered(self, mocker):
        """Test when all options are filtered out by max cost"""
        mock_pricing = {
            "n2d": {
                "spot_core": 1.0,  # Very expensive
                "spot_ram": 1.0,
                "ondemand_core": 2.0,
                "ondemand_ram": 2.0,
            },
        }

        mocker.patch("main.parse_pricing_data", return_value=mock_pricing)

        # Should not raise exception
        generate_compute_class(
            region="europe-north1",
            vcpus=4,
            ram_gb=16,
            max_daily_cost=0.1,  # Impossibly low
        )
