"""Tests for CLI entry point"""
import sys
import pytest
from unittest.mock import MagicMock


class TestMainCLI:
    """Tests for main() CLI function"""

    def test_main_default_arguments(self, mocker):
        """Test main with default arguments"""
        mocker.patch("sys.argv", ["gkecc", "us-central1"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        mock_generate.assert_called_once()
        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["region"] == "us-central1"
        assert call_kwargs["vcpus"] == 4
        assert call_kwargs["ram_gb"] == 16
        assert call_kwargs["arch"] == "amd64"

    def test_main_with_all_options(self, mocker):
        """Test main with all options specified"""
        mocker.patch(
            "sys.argv",
            [
                "gkecc",
                "europe-west1",
                "--vcpus",
                "8",
                "--ram",
                "32",
                "--max-cost",
                "10",
                "--arch",
                "arm",
                "--node-label",
                "env=prod",
                "--refresh",
                "--verbose",
                "-o",
                "output.yaml",
            ],
        )
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        mock_generate.assert_called_once()
        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["region"] == "europe-west1"
        assert call_kwargs["vcpus"] == 8
        assert call_kwargs["ram_gb"] == 32
        assert call_kwargs["max_daily_cost"] == 10
        assert call_kwargs["arch"] == "arm"
        assert call_kwargs["node_labels"] == {"env": "prod"}
        assert call_kwargs["use_cache"] is False
        assert call_kwargs["output_file"] == "output.yaml"

    def test_main_with_multiple_node_labels(self, mocker):
        """Test main with multiple node labels"""
        mocker.patch(
            "sys.argv",
            [
                "gkecc",
                "us-central1",
                "--node-label",
                "env=prod,team=platform",
                "--node-label",
                "owner=devops",
            ],
        )
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["node_labels"] == {
            "env": "prod",
            "team": "platform",
            "owner": "devops",
        }

    def test_main_invalid_node_label(self, mocker, capsys):
        """Test main with invalid node label format"""
        mocker.patch(
            "sys.argv",
            ["gkecc", "us-central1", "--node-label", "invalid"],
        )

        from main import main

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "Invalid label format" in captured.out

    def test_main_exception_handling(self, mocker, capsys):
        """Test main handles exceptions"""
        mocker.patch("sys.argv", ["gkecc", "us-central1"])
        mocker.patch(
            "main.generate_compute_class", side_effect=Exception("Test error")
        )

        from main import main

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "Error: Test error" in captured.out

    def test_main_table_format(self, mocker):
        """Test main with table format"""
        mocker.patch(
            "sys.argv", ["gkecc", "us-central1", "--format", "table"]
        )
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        # Check that FORMAT global is set
        import main as main_module

        main()

        assert main_module.FORMAT == "table"

    def test_main_verbose_flag(self, mocker):
        """Test main with verbose flag"""
        mocker.patch("sys.argv", ["gkecc", "us-central1", "--verbose"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        # Check that VERBOSE global is set
        import main as main_module

        main()

        assert main_module.VERBOSE is True

    def test_main_no_region_uses_default(self, mocker):
        """Test main without region uses default"""
        mocker.patch("sys.argv", ["gkecc"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["region"] == "europe-north1"
