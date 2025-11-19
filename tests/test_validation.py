"""Tests for machine type validation"""
import pytest
from unittest.mock import MagicMock, Mock
from main import validate_machine_compatibility


class TestValidateMachineCompatibility:
    """Tests for validate_machine_compatibility function"""

    def test_validate_with_predefined_machine_types(self, mocker):
        """Test validation with predefined machine types"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value

        # Create mock machine types
        mock_machine_type_n2 = Mock()
        mock_machine_type_n2.name = "n2-standard-4"
        mock_machine_type_n2.guest_cpus = 4
        mock_machine_type_n2.memory_mb = 16384

        mock_machine_type_t2d = Mock()
        mock_machine_type_t2d.name = "t2d-standard-4"
        mock_machine_type_t2d.guest_cpus = 4
        mock_machine_type_t2d.memory_mb = 16384

        mock_instance.list.return_value = [mock_machine_type_n2, mock_machine_type_t2d]

        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=16,
            families={"n2", "t2d", "c2d"},
        )

        assert result == {"n2", "t2d"}
        mock_instance.list.assert_called_once()

    def test_validate_with_custom_machine_types(self, mocker):
        """Test validation with custom machine type support"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value

        # Return empty list (no predefined types match)
        mock_instance.list.return_value = []

        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=16,
            families={"n2", "n2d", "e2"},
        )

        # All three families support custom machine types with 4GB/vCPU ratio
        assert result == {"n2", "n2d", "e2"}

    def test_validate_with_custom_ratio_out_of_range(self, mocker):
        """Test validation with custom machine type ratio out of range"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value
        mock_instance.list.return_value = []

        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=32,  # 8GB per vCPU - too high
            families={"n2", "n2d"},
        )

        # Should return empty set as ratio is out of range
        assert result == set()

    def test_validate_with_ram_tolerance(self, mocker):
        """Test validation with RAM tolerance"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value

        # Create mock machine type with slightly different RAM
        mock_machine_type = Mock()
        mock_machine_type.name = "n2-standard-4"
        mock_machine_type.guest_cpus = 4
        mock_machine_type.memory_mb = 16000  # Slightly less than 16384

        mock_instance.list.return_value = [mock_machine_type]

        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=16,
            families={"n2"},
        )

        # Should match within tolerance
        assert result == {"n2"}

    def test_validate_incompatible_families(self, mocker):
        """Test validation with incompatible families"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value

        # Only return one matching machine type
        mock_machine_type = Mock()
        mock_machine_type.name = "n2-standard-4"
        mock_machine_type.guest_cpus = 4
        mock_machine_type.memory_mb = 16384

        mock_instance.list.return_value = [mock_machine_type]

        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=16,
            families={"n2", "c4a", "h3"},  # c4a and h3 don't support this config
        )

        # Only n2 should be compatible
        assert result == {"n2"}

    def test_validate_mixed_predefined_and_custom(self, mocker):
        """Test validation with both predefined and custom machine types"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value

        # Return one predefined type
        mock_machine_type = Mock()
        mock_machine_type.name = "t2d-standard-4"
        mock_machine_type.guest_cpus = 4
        mock_machine_type.memory_mb = 16384

        mock_instance.list.return_value = [mock_machine_type]

        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=16,
            families={"t2d", "n2", "n2d"},
        )

        # t2d has predefined type, n2 and n2d support custom
        assert result == {"t2d", "n2", "n2d"}

    def test_validate_no_matching_families(self, mocker):
        """Test validation with no matching families"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value
        mock_instance.list.return_value = []

        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=16,
            families={"c4a", "h3"},  # Families that don't support custom types
        )

        assert result == set()

    def test_validate_custom_ratio_at_boundaries(self, mocker):
        """Test validation with custom machine type ratio at boundaries"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value
        mock_instance.list.return_value = []

        # Test lower boundary (0.9 GB/vCPU)
        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=3.6,  # 0.9 GB per vCPU
            families={"n2"},
        )
        assert result == {"n2"}

        # Test upper boundary (6.5 GB/vCPU)
        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=26,  # 6.5 GB per vCPU
            families={"n2"},
        )
        assert result == {"n2"}

        # Test just below lower boundary
        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=3.5,  # 0.875 GB per vCPU
            families={"n2"},
        )
        assert result == set()

        # Test just above upper boundary
        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=26.5,  # 6.625 GB per vCPU
            families={"n2"},
        )
        assert result == set()

    def test_validate_with_unknown_machine_types(self, mocker):
        """Test validation ignores machine types from unknown families"""
        # Mock the Compute Engine client
        mock_client = mocker.patch("main.compute_v1.MachineTypesClient")
        mock_instance = mock_client.return_value

        # Return machine types including ones from unknown families
        mock_machine_type_n2 = Mock()
        mock_machine_type_n2.name = "n2-standard-4"
        mock_machine_type_n2.guest_cpus = 4
        mock_machine_type_n2.memory_mb = 16384

        mock_machine_type_unknown = Mock()
        mock_machine_type_unknown.name = "unknown-type-4"
        mock_machine_type_unknown.guest_cpus = 4
        mock_machine_type_unknown.memory_mb = 16384

        mock_instance.list.return_value = [mock_machine_type_n2, mock_machine_type_unknown]

        result = validate_machine_compatibility(
            project="test-project",
            region="us-central1",
            vcpus=4,
            ram_gb=16,
            families={"n2", "t2d"},
        )

        # Should only match n2, ignore unknown family
        assert result == {"n2"}
