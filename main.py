#!/usr/bin/env python3
"""
Generate a GKE ComputeClass spec using Cloud Billing Catalog API.
Prioritises cheapest total cost (cores + RAM) for spot, then on-demand.
"""
import sys
import re
import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from google.cloud import billing_v1
from google.cloud import compute_v1


# ANSI color codes
class Colors:
    GREY = "\033[90m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"


VERBOSE = False
FORMAT = "computeclass"


def log(msg, color=Colors.GREY, verbose_only=True):
    """Print colored message to stderr"""
    if verbose_only and not VERBOSE:
        return
    print(f"{color}{msg}{Colors.RESET}", file=sys.stderr)


# ARM-based machine families
ARM_FAMILIES = {"t2a", "c4a"}
# AMD64-based machine families (all others are AMD64)
AMD64_FAMILIES = None  # None means "not in ARM_FAMILIES"

# Machine family categories
MACHINE_CATEGORIES = {
    "general-purpose": {"n1", "n2", "n2d", "n4", "e2", "t2d", "t2a"},
    "compute-optimised": {"c2", "c2d", "c3", "c3d", "c4", "c4a", "c4d", "h3"},
    "memory-optimised": {"m1", "m2", "m3", "m4"},
    "storage-optimised": {"z3"},
    "gpu": {"a2", "a3", "g1", "g2"},
}


def get_cache_dir():
    """Get cache directory path"""
    cache_dir = Path.home() / ".cache" / "gkecc"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def load_sku_cache():
    """Load cached SKU data if it's from today"""
    cache_file = get_cache_dir() / "skus.json"
    if cache_file.exists():
        try:
            with open(cache_file, "r") as f:
                cache_data = json.load(f)

                # Check if cache is from today
                cached_date = cache_data.get("date")
                today = datetime.now().strftime("%Y-%m-%d")

                if cached_date == today:
                    log(f"✓ Loaded SKU data from cache ({cached_date})", Colors.GREEN)
                    return cache_data["skus"]
                else:
                    log(
                        f"Cache is stale (from {cached_date}), fetching fresh data",
                        Colors.YELLOW,
                    )
                    return None
        except Exception as e:
            log(f"Failed to load SKU cache: {e}", Colors.YELLOW)
    return None


def save_sku_cache(sku_data):
    """Save SKU data to cache with today's date"""
    cache_file = get_cache_dir() / "skus.json"
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        cache_data = {"date": today, "skus": sku_data}
        with open(cache_file, "w") as f:
            json.dump(cache_data, f, indent=2)
        log(f"✓ Saved SKU data to cache ({today})", Colors.GREEN)
    except Exception as e:
        log(f"Failed to save SKU cache: {e}", Colors.YELLOW)


def extract_machine_family(description):
    """Extract machine family from SKU description"""
    desc_lower = description.lower()

    # Pattern matching for different machine types
    patterns = {
        "n2d": r"\bn2d\b",
        "n2": r"\bn2(?!d)\b",
        "n1": r"\bn1\b",
        "n4": r"\bn4\b",
        "e2": r"\be2\b",
        "c2d": r"\bc2d\b",
        "c2": r"\bc2(?!d)\b",
        "c3d": r"\bc3d\b",
        "c3": r"\bc3(?!d)\b",
        "c4a": r"\bc4a\b",
        "c4d": r"\bc4d\b",
        "c4": r"\bc4(?![ad])\b",
        "t2a": r"\bt2a\b",
        "t2d": r"\bt2d\b",
        "m1": r"\bm1\b",
        "m2": r"\bm2\b",
        "m3": r"\bm3\b",
        "m4": r"\bm4\b",
        "a2": r"\ba2\b",
        "a3": r"\ba3\b",
        "g1": r"\bg1\b",
        "g2": r"\bg2\b",
        "h3": r"\bh3\b",
        "z3": r"\bz3\b",
    }

    for family, pattern in patterns.items():
        if re.search(pattern, desc_lower):
            return family

    return None


def parse_pricing_data(region="europe-north1", arch="amd64", use_cache=True):
    """Fetch and parse pricing data from Cloud Billing API"""

    # Try to load from cache first
    sku_data_list = None
    if use_cache:
        sku_data_list = load_sku_cache()

    # Fetch from API if not cached
    if sku_data_list is None:
        log("Fetching pricing from GCP Billing API...", Colors.BLUE)

        client = billing_v1.CloudCatalogClient()

        # Find Compute Engine service
        services = client.list_services()
        compute_service = None

        for service in services:
            if "Compute Engine" in service.display_name:
                compute_service = service
                log(f"Found service: {service.display_name}", Colors.GREY)
                break

        if not compute_service:
            raise Exception("Compute Engine service not found")

        log("Fetching all Compute Engine SKUs...", Colors.BLUE)

        # Fetch all SKUs and convert to JSON-serializable format
        skus = client.list_skus(parent=compute_service.name)
        sku_data_list = []

        for sku in skus:
            # Extract price
            price = None
            for pricing_info in sku.pricing_info:
                for tier in pricing_info.pricing_expression.tiered_rates:
                    price = tier.unit_price.units + (tier.unit_price.nanos / 1e9)
                    break
                if price:
                    break

            if price is not None:
                sku_data_list.append(
                    {
                        "description": sku.description,
                        "regions": list(sku.service_regions),
                        "price": price,
                    }
                )

        log(f"Fetched {len(sku_data_list)} SKUs", Colors.GREY)

        # Save to cache
        save_sku_cache(sku_data_list)

    # Now process the cached/fetched SKU data for the specific region and arch
    log(f"Processing SKUs for {region} ({arch})...", Colors.BLUE)

    spot_core_prices = defaultdict(list)
    spot_ram_prices = defaultdict(list)
    ondemand_core_prices = defaultdict(list)
    ondemand_ram_prices = defaultdict(list)

    matched_count = 0

    for sku_data in sku_data_list:
        desc = sku_data["description"].lower()

        # Filter for region
        if not any(r.lower() == region.lower() for r in sku_data["regions"]):
            continue

        # Determine if spot or on-demand
        is_spot = "spot" in desc or "preemptible" in desc

        # Skip if not compute instance pricing
        if "instance" not in desc:
            continue

        # Skip custom instances
        if "custom" in desc:
            continue

        # Determine if core or RAM
        is_core = "core" in desc and "running" in desc
        is_ram = "ram" in desc and "running" in desc

        if not (is_core or is_ram):
            continue

        # Extract machine family
        family = extract_machine_family(desc)
        if not family:
            continue

        # Filter by architecture
        is_arm = family in ARM_FAMILIES
        if arch == "amd64" and is_arm:
            continue
        elif arch == "arm" and not is_arm:
            continue

        price = sku_data["price"]
        matched_count += 1

        # Store prices by family
        if is_spot and is_core:
            spot_core_prices[family].append(price)
        elif is_spot and is_ram:
            spot_ram_prices[family].append(price)
        elif not is_spot and is_core:
            ondemand_core_prices[family].append(price)
        elif not is_spot and is_ram:
            ondemand_ram_prices[family].append(price)

    log(f"Matched {matched_count} relevant pricing entries for {region}", Colors.GREY)

    # Average prices for each family
    pricing = {}

    for family in set(list(spot_core_prices.keys()) + list(spot_ram_prices.keys())):
        if (
            family in spot_core_prices
            and family in spot_ram_prices
            and family in ondemand_core_prices
            and family in ondemand_ram_prices
        ):
            pricing[family] = {
                "spot_core": sum(spot_core_prices[family])
                / len(spot_core_prices[family]),
                "spot_ram": sum(spot_ram_prices[family]) / len(spot_ram_prices[family]),
                "ondemand_core": sum(ondemand_core_prices[family])
                / len(ondemand_core_prices[family]),
                "ondemand_ram": sum(ondemand_ram_prices[family])
                / len(ondemand_ram_prices[family]),
            }

    return pricing


def validate_machine_compatibility(project, region, vcpus, ram_gb, families):
    """
    Validate which machine families support the requested vCPU/RAM combination.
    Returns a set of compatible family names.
    Raises an exception if validation fails.
    """
    log(f"Validating machine type compatibility for {vcpus} vCPU + {ram_gb}GB RAM...", Colors.BLUE)

    # Extract zone from region (use first zone in region)
    zone = f"{region}-a"

    client = compute_v1.MachineTypesClient()
    request = compute_v1.ListMachineTypesRequest(
        project=project,
        zone=zone,
    )

    compatible_families = set()

    # Get all machine types for this zone
    machine_types = client.list(request=request)

    for machine_type in machine_types:
        # Extract family from machine type name (e.g., "n2-standard-4" -> "n2")
        type_name = machine_type.name.lower()
        family = None

        # Match against known families
        for fam in families:
            if type_name.startswith(f"{fam}-"):
                family = fam
                break

        if not family:
            continue

        # Check if this machine type matches our requirements
        # Account for slight variations in RAM (GCP uses 1024-based GB)
        ram_mb = ram_gb * 1024
        tolerance = 512  # 512MB tolerance

        if (machine_type.guest_cpus == vcpus and
            abs(machine_type.memory_mb - ram_mb) <= tolerance):
            compatible_families.add(family)
            log(f"  ✓ {family}: {machine_type.name} ({machine_type.guest_cpus} vCPU, {machine_type.memory_mb}MB)", Colors.GREEN)

    # Check for custom machine type support
    # Most families support custom configurations within certain ratios
    custom_families = set()
    for family in families:
        if family not in compatible_families:
            # Check if family supports custom machine types
            # Custom machines allow flexible vCPU/RAM within limits
            if family in {"n1", "n2", "n2d", "e2", "m1", "m2", "m3"}:
                # These families support custom machine types
                # Check if ratio is within acceptable range
                gb_per_vcpu = ram_gb / vcpus
                # Most families support 0.9GB to 6.5GB per vCPU
                if 0.9 <= gb_per_vcpu <= 6.5:
                    custom_families.add(family)
                    log(f"  ✓ {family}: custom machine type ({vcpus} vCPU, {ram_gb}GB, {gb_per_vcpu:.1f}GB/vCPU)", Colors.GREEN)

    compatible_families.update(custom_families)

    incompatible = set(families) - compatible_families
    if incompatible:
        log(f"  ✗ Incompatible families (will be skipped by GKE): {', '.join(sorted(incompatible))}", Colors.YELLOW)

    return compatible_families


def calculate_costs(pricing, vcpus, ram_gb):
    """Calculate total costs for all machine families"""
    all_options = []
    for family, prices in pricing.items():
        spot_total = (prices["spot_core"] * vcpus) + (prices["spot_ram"] * ram_gb)
        ondemand_total = (prices["ondemand_core"] * vcpus) + (
            prices["ondemand_ram"] * ram_gb
        )

        # Add spot option
        all_options.append(
            {
                "family": family,
                "is_spot": True,
                "total": spot_total,
                "core": prices["spot_core"],
                "ram": prices["spot_ram"],
            }
        )

        # Add on-demand option
        all_options.append(
            {
                "family": family,
                "is_spot": False,
                "total": ondemand_total,
                "core": prices["ondemand_core"],
                "ram": prices["ondemand_ram"],
            }
        )

    return all_options


def filter_by_max_cost(options, max_daily_cost):
    """Filter options by maximum daily cost"""
    if not max_daily_cost:
        return options
    return [opt for opt in options if opt["total"] * 24 <= max_daily_cost]


def filter_by_category(options, categories):
    """Filter options by machine family categories (can be multiple)"""
    if not categories:
        return options

    # Combine allowed families from all specified categories
    allowed_families = set()
    if isinstance(categories, str):
        # Single category (for backwards compatibility)
        allowed_families = MACHINE_CATEGORIES.get(categories, set())
    else:
        # Multiple categories
        for category in categories:
            allowed_families.update(MACHINE_CATEGORIES.get(category, set()))

    return [opt for opt in options if opt["family"] in allowed_families]


def format_comparison(daily_cost, cheapest_daily):
    """Format cost comparison string"""
    if daily_cost == cheapest_daily:
        return "(cheapest)"
    multiplier = daily_cost / cheapest_daily
    return f"({multiplier:.1f}x)"


def generate_yaml_output(
    region, arch, max_daily_cost, node_labels, sorted_options, vcpus, ram_gb, categories=None, name=None
):
    """Generate YAML ComputeClass specification"""
    lines = []
    lines.append("apiVersion: cloud.google.com/v1\n")
    lines.append("kind: ComputeClass\n")
    lines.append("metadata:\n")

    # Generate name with categories if not overridden
    if name:
        # Use user-provided name
        pass
    elif categories:
        if isinstance(categories, str):
            categories = [categories]
        # Sort for consistent naming and use abbreviations for shorter names
        category_abbrev = {
            "general-purpose": "gp",
            "compute-optimised": "co",
            "memory-optimised": "mo",
            "storage-optimised": "so",
            "gpu": "gpu",
        }
        sorted_cats = sorted(categories)
        abbreviated = [category_abbrev.get(cat, cat) for cat in sorted_cats]
        category_part = "-".join(abbreviated)
        name = f"{category_part}-{region}"
    else:
        name = region
    lines.append(f"  name: {name}\n")

    lines.append("spec:\n")
    arch_label = arch.upper()
    description = f"{arch_label}"
    if categories:
        if isinstance(categories, str):
            categories = [categories]
        sorted_cats = sorted(categories)
        description += f" {'+'.join(sorted_cats)}"
    description += f" for {region}"
    if max_daily_cost:
        description += f", max ${max_daily_cost}/day"
    lines.append(f'  description: "{description}"\n')
    lines.append("  whenUnsatisfiable: ScaleUpAnyway\n")
    lines.append("  nodePoolAutoCreation:\n")
    lines.append("    enabled: true\n")
    if node_labels:
        lines.append("    nodeLabels:\n")
        for key, value in node_labels.items():
            lines.append(f'      {key}: "{value}"\n')
    lines.append("  priorities:\n")

    cheapest_daily = sorted_options[0]["total"] * 24 if sorted_options else 0

    # Write all options sorted by total cost
    for i, opt in enumerate(sorted_options):
        spot_str = "true" if opt["is_spot"] else "false"
        type_label = "spot" if opt["is_spot"] else "on-demand"
        daily_cost = opt["total"] * 24

        # Generate example machine type name
        ram_mb = ram_gb * 1024
        machine_type = f"{opt['family']}-custom-{vcpus}-{ram_mb}"

        if i == 0:
            comment = (
                f"${daily_cost:.2f}/day ({machine_type}, {type_label}, cheapest)"
            )
        else:
            multiplier = daily_cost / cheapest_daily
            comment = f"${daily_cost:.2f}/day ({machine_type}, {type_label}, {multiplier:.1f}x)"

        lines.append(f"  - machineFamily: {opt['family']}  # {comment}\n")
        lines.append(f"    spot: {spot_str}\n")

    return "".join(lines)


def format_table_output(sorted_options, vcpus, ram_gb):
    """Format table output for pricing options"""
    cheapest_daily = sorted_options[0]["total"] * 24 if sorted_options else 0
    lines = []
    lines.append(f"{'Family':<10} {'Type':<10} {'Daily Cost':>12} {'Comparison':>12}\n")
    lines.append("-" * 50 + "\n")
    for opt in sorted_options:
        spot_label = "spot" if opt["is_spot"] else "on-demand"
        daily_cost = opt["total"] * 24
        comparison = format_comparison(daily_cost, cheapest_daily)
        lines.append(
            f"{opt['family']:<10} {spot_label:<10} ${daily_cost:>10.2f}/day  {comparison:>12}\n"
        )
    return "".join(lines)


def parse_node_labels(label_list):
    """Parse node labels from CLI arguments"""
    node_labels = {}
    if not label_list:
        return node_labels

    for label_group in label_list:
        # Split by comma to support multiple labels in one arg
        for label in label_group.split(","):
            label = label.strip()
            if not label:
                continue
            if "=" not in label:
                raise ValueError(f"Invalid label format '{label}'. Expected KEY=VALUE")
            key, value = label.split("=", 1)
            node_labels[key] = value

    return node_labels


def generate_compute_class(
    region="europe-north1",
    output_file=None,
    vcpus=4,
    ram_gb=16,
    max_daily_cost=None,
    arch="amd64",
    use_cache=True,
    node_labels=None,
    category=None,
    name=None,
    validate=False,
    project=None,
):
    """Generate compute class spec from API pricing"""

    pricing = parse_pricing_data(region, arch=arch, use_cache=use_cache)

    if not pricing:
        log("No pricing data found!", Colors.YELLOW)
        return

    log(f"Found {len(pricing)} machine families with complete pricing", Colors.GREEN)

    # Validate machine type compatibility if requested
    if validate:
        compatible_families = validate_machine_compatibility(project, region, vcpus, ram_gb, set(pricing.keys()))
        # Filter pricing to only compatible families
        pricing = {family: prices for family, prices in pricing.items() if family in compatible_families}
        if not pricing:
            log("No compatible machine families found for the specified vCPU/RAM combination!", Colors.YELLOW)
            return
        log(f"Using {len(pricing)} compatible machine families", Colors.GREEN)

    # Calculate total costs and create entries for both spot and on-demand
    all_options = calculate_costs(pricing, vcpus, ram_gb)

    # Sort by total cost (cheapest first)
    all_sorted = sorted(all_options, key=lambda x: x["total"])

    # Filter by category if specified
    all_sorted = filter_by_category(all_sorted, category)
    if category:
        if isinstance(category, list):
            cat_str = ", ".join(category)
            log(f"Filtered to {cat_str} instances", Colors.YELLOW)
        else:
            log(f"Filtered to {category} instances", Colors.YELLOW)

    # Filter by max daily cost if specified
    all_sorted = filter_by_max_cost(all_sorted, max_daily_cost)
    if max_daily_cost:
        log(f"Filtered to options under ${max_daily_cost}/day", Colors.YELLOW)

    if not all_sorted:
        log("No options match the criteria!", Colors.YELLOW)
        return

    cheapest_daily = all_sorted[0]["total"] * 24

    # Output table if format is table
    if FORMAT == "table":
        log(
            f"Options sorted by total cost for {vcpus} vCPU + {ram_gb}GB RAM (per day, USD):",
            Colors.BLUE,
        )
        print(format_table_output(all_sorted, vcpus, ram_gb), end="")
        return

    # Show verbose pricing info
    log(
        f"All options sorted by total cost for {vcpus} vCPU + {ram_gb}GB RAM (per day, USD):",
        Colors.BLUE,
    )
    for opt in all_sorted:
        spot_label = "spot" if opt["is_spot"] else "on-demand"
        daily_cost = opt["total"] * 24
        comparison = format_comparison(daily_cost, cheapest_daily)
        log(
            f"  {opt['family']:10} {spot_label:10} ${daily_cost:.2f}/day  {comparison}",
            Colors.GREY,
        )

    # Generate YAML
    yaml_output = generate_yaml_output(
        region, arch, max_daily_cost, node_labels, all_sorted, vcpus, ram_gb, category, name
    )

    output = sys.stdout if output_file is None else open(output_file, "w")
    try:
        output.write(yaml_output)
    finally:
        if output_file is not None:
            output.close()

    if output_file:
        log(f"\n✓ Generated compute class spec: {output_file}", Colors.GREEN)
    else:
        log("\n✓ Generated compute class spec", Colors.GREEN)


def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate GKE ComputeClass spec with cost-optimised machine priorities using GCP Cloud Billing API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s europe-north1 > config.yaml                  # Output to file (uses cache)
  %(prog)s europe-north1 --refresh                      # Refresh pricing from API
  %(prog)s europe-north1 --max-cost 5 -o config.yaml   # Cap at $5/day, save to file
  %(prog)s europe-north1 --arch arm --max-cost 3        # ARM instances only
  %(prog)s us-central1 --max-cost 10 --vcpus 8 --ram 32 # Custom instance size
  %(prog)s europe-north1 2>/dev/null | kubectl apply -f - # Suppress logs, apply directly

Notes:
  - Pricing data is cached in ~/.cache/gkecc/ for faster subsequent runs
  - Requires Cloud Billing API enabled: gcloud services enable cloudbilling.googleapis.com
  - Requires authentication: gcloud auth application-default login
  - Use --arch to select AMD64 (default) or ARM instances
  - Interleaves spot and on-demand by total cost for optimal price/performance
        """,
    )

    parser.add_argument(
        "--region",
        type=str,
        default="europe-north1",
        help="GCP region (default: europe-north1)",
    )

    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Override ComputeClass name (default: auto-generated from categories and region)",
    )

    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        metavar="DOLLARS",
        help="Maximum daily cost in USD for a single instance (default: no limit)",
    )

    parser.add_argument(
        "--vcpus",
        type=int,
        default=4,
        metavar="N",
        help="Number of vCPUs for cost calculation (default: 4)",
    )

    parser.add_argument(
        "--ram",
        type=int,
        default=16,
        metavar="GB",
        help="RAM in GB for cost calculation (default: 16)",
    )

    parser.add_argument(
        "--arch",
        choices=["amd64", "arm"],
        default="amd64",
        help="CPU architecture to include (default: amd64)",
    )

    # Category selection - can specify multiple
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include all machine categories",
    )
    parser.add_argument(
        "--general-purpose",
        action="store_true",
        help="Include general-purpose machines (n1, n2, n2d, n4, e2, t2d, t2a) [default if no categories specified]",
    )
    parser.add_argument(
        "--compute-optimised",
        action="store_true",
        help="Include compute-optimised machines (c2, c2d, c3, c3d, c4, c4a, c4d, h3)",
    )
    parser.add_argument(
        "--memory-optimised",
        action="store_true",
        help="Include memory-optimised machines (m1, m2, m3, m4)",
    )
    parser.add_argument(
        "--storage-optimised",
        action="store_true",
        help="Include storage-optimised machines (z3)",
    )
    parser.add_argument(
        "--gpu",
        action="store_true",
        help="Include GPU machines (a2, a3, g1, g2)",
    )

    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh pricing cache from API (ignore cached data)",
    )

    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip validation of machine families (validation is enabled by default)",
    )

    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="GCP project ID (required for validation, defaults to GOOGLE_CLOUD_PROJECT or GCLOUD_PROJECT env var)",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show debug logs",
    )

    parser.add_argument(
        "--format",
        choices=["table", "computeclass"],
        default="computeclass",
        help="Output format: table or computeclass (YAML, default)",
    )

    parser.add_argument(
        "--node-label",
        action="append",
        metavar="KEY=VALUE",
        help="Node label(s) to apply. Supports comma-separated values (e.g., --node-label workload=core,env=production) or multiple flags (e.g., --node-label team=platform --node-label env=production)",
    )

    parser.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FILE",
        help="Output YAML file (default: stdout)",
    )

    args = parser.parse_args()

    global VERBOSE, FORMAT
    VERBOSE = args.verbose
    FORMAT = args.format

    try:
        # Get project from args or environment
        import os
        project = args.project or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCLOUD_PROJECT")

        # Validation is enabled by default unless --skip-validation is specified
        validate = not args.skip_validation

        # If validation is enabled, project is required
        if validate and not project:
            print("Error: Validation requires a GCP project ID. Specify --project or set GOOGLE_CLOUD_PROJECT environment variable, or use --skip-validation to skip validation.")
            exit(1)

        # Parse node labels
        node_labels = parse_node_labels(args.node_label)

        # Collect selected categories
        categories = []

        # If --all is specified, include all categories
        if args.all:
            categories = ["general-purpose", "compute-optimised", "memory-optimised", "storage-optimised", "gpu"]
        else:
            if args.general_purpose:
                categories.append("general-purpose")
            if args.compute_optimised:
                categories.append("compute-optimised")
            if args.memory_optimised:
                categories.append("memory-optimised")
            if args.storage_optimised:
                categories.append("storage-optimised")
            if args.gpu:
                categories.append("gpu")

            # Default to general-purpose if no categories specified
            if not categories:
                categories = ["general-purpose"]

        generate_compute_class(
            region=args.region,
            output_file=args.output,
            vcpus=args.vcpus,
            ram_gb=args.ram,
            max_daily_cost=args.max_cost,
            arch=args.arch,
            use_cache=not args.refresh,
            node_labels=node_labels if node_labels else None,
            category=categories,
            name=args.name,
            validate=validate,
            project=project,
        )
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        exit(1)


if __name__ == "__main__":
    main()
