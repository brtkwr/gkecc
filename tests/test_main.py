"""Tests for gkecc main module"""
import pytest
from main import (
    extract_machine_family,
    calculate_costs,
    filter_by_max_cost,
    filter_by_category,
    format_comparison,
    generate_yaml_output,
    format_table_output,
    parse_node_labels,
)


class TestExtractMachineFamily:
    """Tests for extract_machine_family function"""

    def test_extract_n2d(self):
        assert extract_machine_family("N2D Instance Core") == "n2d"
        assert extract_machine_family("n2d instance ram") == "n2d"

    def test_extract_n2(self):
        assert extract_machine_family("N2 Instance Core") == "n2"
        assert extract_machine_family("n2 instance ram") == "n2"

    def test_extract_t2d(self):
        assert extract_machine_family("T2D AMD Instance Core") == "t2d"

    def test_extract_c4a(self):
        assert extract_machine_family("C4A ARM Instance Core") == "c4a"

    def test_extract_c3d(self):
        assert extract_machine_family("C3D Instance Core") == "c3d"

    def test_extract_m4(self):
        assert extract_machine_family("M4 Memory-optimized Instance Core") == "m4"

    def test_extract_none(self):
        assert extract_machine_family("Some Unknown Instance") is None


class TestCalculateCosts:
    """Tests for calculate_costs function"""

    def test_calculate_costs_single_family(self):
        pricing = {
            "n2d": {
                "spot_core": 0.01,
                "spot_ram": 0.001,
                "ondemand_core": 0.02,
                "ondemand_ram": 0.002,
            }
        }
        result = calculate_costs(pricing, vcpus=4, ram_gb=16)

        assert len(result) == 2  # spot and on-demand
        assert result[0]["family"] == "n2d"
        assert result[0]["is_spot"] is True
        assert result[0]["total"] == 0.04 + 0.016  # 4 * 0.01 + 16 * 0.001
        assert result[1]["family"] == "n2d"
        assert result[1]["is_spot"] is False
        assert result[1]["total"] == 0.08 + 0.032  # 4 * 0.02 + 16 * 0.002

    def test_calculate_costs_multiple_families(self):
        pricing = {
            "n2d": {
                "spot_core": 0.01,
                "spot_ram": 0.001,
                "ondemand_core": 0.02,
                "ondemand_ram": 0.002,
            },
            "t2d": {
                "spot_core": 0.005,
                "spot_ram": 0.0005,
                "ondemand_core": 0.01,
                "ondemand_ram": 0.001,
            },
        }
        result = calculate_costs(pricing, vcpus=2, ram_gb=8)

        assert len(result) == 4  # 2 families * 2 types
        # Check that we have both families
        families = {opt["family"] for opt in result}
        assert families == {"n2d", "t2d"}

    def test_calculate_costs_zero_resources(self):
        pricing = {
            "n2d": {
                "spot_core": 0.01,
                "spot_ram": 0.001,
                "ondemand_core": 0.02,
                "ondemand_ram": 0.002,
            }
        }
        result = calculate_costs(pricing, vcpus=0, ram_gb=0)
        assert result[0]["total"] == 0
        assert result[1]["total"] == 0


class TestFilterByMaxCost:
    """Tests for filter_by_max_cost function"""

    def test_filter_no_max_cost(self):
        options = [
            {"total": 0.1},
            {"total": 0.2},
            {"total": 0.3},
        ]
        result = filter_by_max_cost(options, None)
        assert len(result) == 3

    def test_filter_with_max_cost(self):
        options = [
            {"total": 0.1},  # 2.4/day
            {"total": 0.2},  # 4.8/day
            {"total": 0.3},  # 7.2/day
        ]
        result = filter_by_max_cost(options, 5.0)
        assert len(result) == 2
        assert result[0]["total"] == 0.1
        assert result[1]["total"] == 0.2

    def test_filter_all_excluded(self):
        options = [
            {"total": 0.5},  # 12/day
            {"total": 0.6},  # 14.4/day
        ]
        result = filter_by_max_cost(options, 5.0)
        assert len(result) == 0


class TestFormatComparison:
    """Tests for format_comparison function"""

    def test_cheapest(self):
        assert format_comparison(1.0, 1.0) == "(cheapest)"

    def test_more_expensive(self):
        assert format_comparison(2.0, 1.0) == "(2.0x)"
        assert format_comparison(1.5, 1.0) == "(1.5x)"
        assert format_comparison(3.7, 1.0) == "(3.7x)"

    def test_rounding(self):
        # Should round to 1 decimal
        assert format_comparison(1.56, 1.0) == "(1.6x)"
        assert format_comparison(1.54, 1.0) == "(1.5x)"


class TestFilterByCategory:
    """Tests for filter_by_category function"""

    def test_filter_no_category(self):
        options = [
            {"family": "t2d", "total": 0.1},
            {"family": "c2d", "total": 0.2},
            {"family": "m3", "total": 0.3},
        ]
        result = filter_by_category(options, None)
        assert len(result) == 3

    def test_filter_general_purpose(self):
        options = [
            {"family": "t2d", "total": 0.1},
            {"family": "c2d", "total": 0.2},
            {"family": "m3", "total": 0.3},
        ]
        result = filter_by_category(options, "general-purpose")
        assert len(result) == 1
        assert result[0]["family"] == "t2d"

    def test_filter_compute_optimised(self):
        options = [
            {"family": "t2d", "total": 0.1},
            {"family": "c2d", "total": 0.2},
            {"family": "c3", "total": 0.25},
            {"family": "m3", "total": 0.3},
        ]
        result = filter_by_category(options, "compute-optimised")
        assert len(result) == 2
        families = {opt["family"] for opt in result}
        assert families == {"c2d", "c3"}

    def test_filter_memory_optimised(self):
        options = [
            {"family": "t2d", "total": 0.1},
            {"family": "m3", "total": 0.3},
            {"family": "m4", "total": 0.4},
        ]
        result = filter_by_category(options, "memory-optimised")
        assert len(result) == 2
        families = {opt["family"] for opt in result}
        assert families == {"m3", "m4"}

    def test_filter_gpu(self):
        options = [
            {"family": "t2d", "total": 0.1},
            {"family": "a2", "total": 0.5},
            {"family": "g2", "total": 0.6},
        ]
        result = filter_by_category(options, "gpu")
        assert len(result) == 2
        families = {opt["family"] for opt in result}
        assert families == {"a2", "g2"}

    def test_filter_invalid_category(self):
        options = [
            {"family": "t2d", "total": 0.1},
        ]
        result = filter_by_category(options, "invalid-category")
        assert len(result) == 0

    def test_filter_multiple_categories(self):
        options = [
            {"family": "t2d", "total": 0.1},
            {"family": "c2d", "total": 0.2},
            {"family": "m3", "total": 0.3},
        ]
        result = filter_by_category(options, ["general-purpose", "memory-optimised"])
        assert len(result) == 2
        families = {opt["family"] for opt in result}
        assert families == {"t2d", "m3"}


class TestParseNodeLabels:
    """Tests for parse_node_labels function"""

    def test_parse_none(self):
        assert parse_node_labels(None) == {}

    def test_parse_empty_list(self):
        assert parse_node_labels([]) == {}

    def test_parse_single_label(self):
        result = parse_node_labels(["key=value"])
        assert result == {"key": "value"}

    def test_parse_multiple_labels(self):
        result = parse_node_labels(["key1=value1", "key2=value2"])
        assert result == {"key1": "value1", "key2": "value2"}

    def test_parse_comma_separated(self):
        result = parse_node_labels(["key1=value1,key2=value2"])
        assert result == {"key1": "value1", "key2": "value2"}

    def test_parse_mixed_format(self):
        result = parse_node_labels(["key1=value1,key2=value2", "key3=value3"])
        assert result == {"key1": "value1", "key2": "value2", "key3": "value3"}

    def test_parse_with_spaces(self):
        result = parse_node_labels(["key1=value1 , key2=value2"])
        assert result == {"key1": "value1", "key2": "value2"}

    def test_parse_with_equals_in_value(self):
        result = parse_node_labels(["key=value=with=equals"])
        assert result == {"key": "value=with=equals"}

    def test_parse_invalid_format(self):
        with pytest.raises(ValueError, match="Invalid label format"):
            parse_node_labels(["invalid"])

    def test_parse_empty_string(self):
        # Empty strings should be ignored
        result = parse_node_labels(["key1=value1,,key2=value2"])
        assert result == {"key1": "value1", "key2": "value2"}


class TestGenerateYamlOutput:
    """Tests for generate_yaml_output function"""

    def test_basic_yaml(self):
        options = [
            {"family": "t2d", "is_spot": True, "total": 0.02},
            {"family": "n2d", "is_spot": False, "total": 0.04},
        ]
        result = generate_yaml_output(
            region="us-central1",
            arch="amd64",
            max_daily_cost=None,
            node_labels=None,
            sorted_options=options,
            vcpus=4,
            ram_gb=16,
        )

        assert "apiVersion: cloud.google.com/v1" in result
        assert "kind: ComputeClass" in result
        assert "name: us-central1" in result
        assert "AMD64 for us-central1" in result
        assert "machineFamily: t2d" in result
        assert "spot: true" in result
        assert "machineFamily: n2d" in result
        assert "spot: false" in result

    def test_yaml_with_node_labels(self):
        options = [{"family": "t2d", "is_spot": True, "total": 0.02}]
        node_labels = {"env": "production", "team": "platform"}
        result = generate_yaml_output(
            region="us-central1",
            arch="amd64",
            max_daily_cost=None,
            node_labels=node_labels,
            sorted_options=options,
            vcpus=4,
            ram_gb=16,
        )

        assert "nodeLabels:" in result
        assert 'env: "production"' in result
        assert 'team: "platform"' in result

    def test_yaml_with_max_cost(self):
        options = [{"family": "t2d", "is_spot": True, "total": 0.02}]
        result = generate_yaml_output(
            region="us-central1",
            arch="amd64",
            max_daily_cost=5.0,
            node_labels=None,
            sorted_options=options,
            vcpus=4,
            ram_gb=16,
        )

        assert "max $5.0/day" in result

    def test_yaml_arm_architecture(self):
        options = [{"family": "t2a", "is_spot": True, "total": 0.02}]
        result = generate_yaml_output(
            region="us-central1",
            arch="arm",
            max_daily_cost=None,
            node_labels=None,
            sorted_options=options,
            vcpus=4,
            ram_gb=16,
        )

        assert "ARM for us-central1" in result

    def test_yaml_cost_comments(self):
        options = [
            {"family": "t2d", "is_spot": True, "total": 0.02},  # $0.48/day
            {"family": "n2d", "is_spot": False, "total": 0.04},  # $0.96/day (2x)
        ]
        result = generate_yaml_output(
            region="us-central1",
            arch="amd64",
            max_daily_cost=None,
            node_labels=None,
            sorted_options=options,
            vcpus=4,
            ram_gb=16,
        )

        assert "cheapest" in result
        assert "2.0x" in result

    def test_yaml_with_custom_name(self):
        options = [{"family": "t2d", "is_spot": True, "total": 0.02}]
        result = generate_yaml_output(
            region="us-central1",
            arch="amd64",
            max_daily_cost=None,
            node_labels=None,
            sorted_options=options,
            vcpus=4,
            ram_gb=16,
            name="custom-compute-class",
        )

        assert "name: custom-compute-class" in result

    def test_yaml_with_categories_list(self):
        options = [{"family": "t2d", "is_spot": True, "total": 0.02}]
        result = generate_yaml_output(
            region="us-central1",
            arch="amd64",
            max_daily_cost=None,
            node_labels=None,
            sorted_options=options,
            vcpus=4,
            ram_gb=16,
            categories=["compute-optimised", "general-purpose"],
        )

        # Should have abbreviated name
        assert "name: co-gp-us-central1" in result
        # Should have full names in description
        assert "compute-optimised+general-purpose" in result

    def test_yaml_with_categories_string(self):
        options = [{"family": "t2d", "is_spot": True, "total": 0.02}]
        result = generate_yaml_output(
            region="us-central1",
            arch="amd64",
            max_daily_cost=None,
            node_labels=None,
            sorted_options=options,
            vcpus=4,
            ram_gb=16,
            categories="memory-optimised",
        )

        # Should have abbreviated name
        assert "name: mo-us-central1" in result
        # Should have full name in description
        assert "memory-optimised" in result


class TestFormatTableOutput:
    """Tests for format_table_output function"""

    def test_basic_table(self):
        options = [
            {"family": "t2d", "is_spot": True, "total": 0.02},
            {"family": "n2d", "is_spot": False, "total": 0.04},
        ]
        result = format_table_output(options, vcpus=4, ram_gb=16)

        assert "Family" in result
        assert "Type" in result
        assert "Daily Cost" in result
        assert "Comparison" in result
        assert "t2d" in result
        assert "n2d" in result
        assert "spot" in result
        assert "on-demand" in result
        assert "(cheapest)" in result
        assert "(2.0x)" in result

    def test_table_formatting(self):
        options = [{"family": "t2d", "is_spot": True, "total": 0.02}]
        result = format_table_output(options, vcpus=4, ram_gb=16)

        lines = result.split("\n")
        # Should have header, separator, and at least one data line
        assert len(lines) >= 3
        assert "---" in lines[1]
