"""Tests for CLI entry point"""
import sys
import pytest
from unittest.mock import MagicMock


class TestMainCLI:
    """Tests for main() CLI function"""

    def test_main_default_arguments(self, mocker):
        """Test main with default arguments"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--skip-validation"])
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
                "--region",
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
                "--skip-validation",
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
                "--region",
                "us-central1",
                "--node-label",
                "env=prod,team=platform",
                "--node-label",
                "owner=devops",
                "--skip-validation",
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
            ["gkecc", "--region", "us-central1", "--node-label", "invalid", "--skip-validation"],
        )

        from main import main

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "Invalid label format" in captured.out

    def test_main_exception_handling(self, mocker, capsys):
        """Test main handles exceptions"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--skip-validation"])
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
            "sys.argv", ["gkecc", "--region", "us-central1", "--format", "table", "--skip-validation"]
        )
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        # Check that FORMAT global is set
        import main as main_module

        main()

        assert main_module.FORMAT == "table"

    def test_main_verbose_flag(self, mocker):
        """Test main with verbose flag"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--verbose", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        # Check that VERBOSE global is set
        import main as main_module

        main()

        assert main_module.VERBOSE is True

    def test_main_no_region_uses_default(self, mocker):
        """Test main without region uses default"""
        mocker.patch("sys.argv", ["gkecc", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["region"] == "europe-north1"

    def test_main_validate_requires_project(self, mocker, capsys):
        """Test that validation without project fails (validation is default)"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1"])

        # Mock only the specific env vars we care about
        original_getenv = mocker.patch.object
        import os
        real_getenv = os.getenv
        def mock_getenv(key, default=None):
            if key in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
                return None
            return real_getenv(key, default)
        mocker.patch("os.getenv", side_effect=mock_getenv)

        from main import main

        with pytest.raises(SystemExit):
            main()

        captured = capsys.readouterr()
        assert "requires a GCP project ID" in captured.out

    def test_main_validate_with_project_flag(self, mocker):
        """Test validation with --project flag (validation is default)"""
        mocker.patch(
            "sys.argv",
            [
                "gkecc",
                "--region",
                "us-central1",
                "--project",
                "my-project",
            ],
        )
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["validate"] is True
        assert call_kwargs["project"] == "my-project"

    def test_main_validate_with_env_var(self, mocker):
        """Test validation with GOOGLE_CLOUD_PROJECT env var (validation is default)"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1"])
        mocker.patch("os.getenv", side_effect=lambda x: "env-project" if x == "GOOGLE_CLOUD_PROJECT" else None)
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["validate"] is True
        assert call_kwargs["project"] == "env-project"

    def test_main_skip_validation(self, mocker):
        """Test --skip-validation flag"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["validate"] is False

    def test_main_with_skip_validation_and_project(self, mocker):
        """Test that project can be specified with --skip-validation"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--skip-validation", "--project", "my-project"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["validate"] is False
        assert call_kwargs["project"] == "my-project"

    def test_main_with_general_purpose_flag(self, mocker):
        """Test --general-purpose flag"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--general-purpose", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["category"] == ["general-purpose"]

    def test_main_with_all_flag(self, mocker):
        """Test --all flag"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--all", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["category"] == ["general-purpose", "compute-optimised", "memory-optimised", "storage-optimised", "gpu"]

    def test_main_with_compute_optimised_flag(self, mocker):
        """Test --compute-optimised flag"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--compute-optimised", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["category"] == ["compute-optimised"]

    def test_main_with_memory_optimised_flag(self, mocker):
        """Test --memory-optimised flag"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--memory-optimised", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["category"] == ["memory-optimised"]

    def test_main_with_storage_optimised_flag(self, mocker):
        """Test --storage-optimised flag"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--storage-optimised", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["category"] == ["storage-optimised"]

    def test_main_with_gpu_flag(self, mocker):
        """Test --gpu flag"""
        mocker.patch("sys.argv", ["gkecc", "--region", "us-central1", "--gpu", "--skip-validation"])
        mock_generate = mocker.patch("main.generate_compute_class")

        from main import main

        main()

        call_kwargs = mock_generate.call_args[1]
        assert call_kwargs["category"] == ["gpu"]
