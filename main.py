#!/usr/bin/env python3
"""
Generate a GKE ComputeClass spec using Cloud Billing Catalog API.
Prioritises cheapest total cost (cores + RAM) for spot, then on-demand.
"""
import sys
import re
from collections import defaultdict
from google.cloud import billing_v1


# ARM-based machine families
ARM_FAMILIES = {'t2a', 'c4a'}
# AMD64-based machine families (all others are AMD64)
AMD64_FAMILIES = None  # None means "not in ARM_FAMILIES"


def extract_machine_family(description):
    """Extract machine family from SKU description"""
    desc_lower = description.lower()

    # Pattern matching for different machine types
    patterns = {
        'n2d': r'\bn2d\b',
        'n2': r'\bn2(?!d)\b',
        'n1': r'\bn1\b',
        'n4': r'\bn4\b',
        'e2': r'\be2\b',
        'c2d': r'\bc2d\b',
        'c2': r'\bc2(?!d)\b',
        'c3d': r'\bc3d\b',
        'c3': r'\bc3(?!d)\b',
        'c4a': r'\bc4a\b',
        'c4d': r'\bc4d\b',
        'c4': r'\bc4(?![ad])\b',
        't2a': r'\bt2a\b',
        't2d': r'\bt2d\b',
        'm1': r'\bm1\b',
        'm2': r'\bm2\b',
        'm3': r'\bm3\b',
        'm4': r'\bm4\b',
        'a2': r'\ba2\b',
        'a3': r'\ba3\b',
        'g1': r'\bg1\b',
        'g2': r'\bg2\b',
        'h3': r'\bh3\b',
        'z3': r'\bz3\b',
    }

    for family, pattern in patterns.items():
        if re.search(pattern, desc_lower):
            return family

    return None


def parse_pricing_data(region='europe-north1', arch='amd64'):
    """Fetch and parse pricing data from Cloud Billing API"""
    client = billing_v1.CloudCatalogClient()

    # Find Compute Engine service
    services = client.list_services()
    compute_service = None

    for service in services:
        if 'Compute Engine' in service.display_name:
            compute_service = service
            print(f"Found service: {service.display_name}", file=sys.stderr)
            break

    if not compute_service:
        raise Exception("Compute Engine service not found")

    print(f"Fetching SKUs for {region}...", file=sys.stderr)

    # Fetch all SKUs
    skus = client.list_skus(parent=compute_service.name)

    spot_core_prices = defaultdict(list)
    spot_ram_prices = defaultdict(list)
    ondemand_core_prices = defaultdict(list)
    ondemand_ram_prices = defaultdict(list)

    sku_count = 0
    matched_count = 0

    for sku in skus:
        sku_count += 1

        desc = sku.description.lower()

        # Filter for region
        if not any(r.lower() == region.lower() for r in sku.service_regions):
            continue

        # Determine if spot or on-demand
        is_spot = 'spot' in desc or 'preemptible' in desc

        # Skip if not compute instance pricing
        if 'instance' not in desc:
            continue

        # Skip custom instances
        if 'custom' in desc:
            continue

        # Determine if core or RAM
        is_core = 'core' in desc and 'running' in desc
        is_ram = 'ram' in desc and 'running' in desc

        if not (is_core or is_ram):
            continue

        # Extract machine family
        family = extract_machine_family(desc)
        if not family:
            continue

        # Filter by architecture
        is_arm = family in ARM_FAMILIES
        if arch == 'amd64' and is_arm:
            continue
        elif arch == 'arm' and not is_arm:
            continue

        # Get price (in USD per hour)
        price = None
        for pricing_info in sku.pricing_info:
            for tier in pricing_info.pricing_expression.tiered_rates:
                # Convert to USD per hour
                # API returns price per GB-hour for RAM or per core-hour
                price_usd = tier.unit_price.units + (tier.unit_price.nanos / 1e9)
                price = price_usd
                break
            if price:
                break

        if price is None:
            continue

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

    print(f"Processed {sku_count} SKUs, matched {matched_count} relevant pricing entries", file=sys.stderr)

    # Average prices for each family
    pricing = {}

    for family in set(list(spot_core_prices.keys()) + list(spot_ram_prices.keys())):
        if (family in spot_core_prices and family in spot_ram_prices and
            family in ondemand_core_prices and family in ondemand_ram_prices):

            pricing[family] = {
                'spot_core': sum(spot_core_prices[family]) / len(spot_core_prices[family]),
                'spot_ram': sum(spot_ram_prices[family]) / len(spot_ram_prices[family]),
                'ondemand_core': sum(ondemand_core_prices[family]) / len(ondemand_core_prices[family]),
                'ondemand_ram': sum(ondemand_ram_prices[family]) / len(ondemand_ram_prices[family]),
            }

    return pricing


def generate_compute_class(region='europe-north1', output_file=None, vcpus=4, ram_gb=16, max_daily_cost=None, arch='amd64'):
    """Generate compute class spec from API pricing"""

    pricing = parse_pricing_data(region, arch=arch)

    if not pricing:
        print("No pricing data found!", file=sys.stderr)
        return

    print(f"\nFound {len(pricing)} machine families with complete pricing", file=sys.stderr)

    # Calculate total costs and create entries for both spot and on-demand
    all_options = []
    for family, prices in pricing.items():
        spot_total = (prices['spot_core'] * vcpus) + (prices['spot_ram'] * ram_gb)
        ondemand_total = (prices['ondemand_core'] * vcpus) + (prices['ondemand_ram'] * ram_gb)

        # Add spot option
        all_options.append({
            'family': family,
            'is_spot': True,
            'total': spot_total,
            'core': prices['spot_core'],
            'ram': prices['spot_ram'],
        })

        # Add on-demand option
        all_options.append({
            'family': family,
            'is_spot': False,
            'total': ondemand_total,
            'core': prices['ondemand_core'],
            'ram': prices['ondemand_ram'],
        })

    # Sort by total cost (cheapest first)
    all_sorted = sorted(all_options, key=lambda x: x['total'])

    # Filter by max daily cost if specified
    if max_daily_cost:
        all_sorted = [opt for opt in all_sorted if opt['total'] * 24 <= max_daily_cost]
        print(f"\nFiltered to options under ${max_daily_cost}/day", file=sys.stderr)

    print(f"\nAll options sorted by total cost for {vcpus} vCPU + {ram_gb}GB RAM (per day, USD):", file=sys.stderr)
    for opt in all_sorted:
        spot_label = "spot" if opt['is_spot'] else "on-demand"
        print(f"  {opt['family']:10} {spot_label:10} ${opt['total']*24:.2f}/day  (${opt['total']:.5f}/hr)", file=sys.stderr)

    # Generate YAML
    output = sys.stdout if output_file is None else open(output_file, 'w')

    try:
        output.write("apiVersion: cloud.google.com/v1\n")
        output.write("kind: ComputeClass\n")
        output.write("metadata:\n")
        output.write(f"  name: cost-optimised-{region}\n")
        output.write("spec:\n")
        arch_label = arch.upper()
        description = f"Cost-optimised {arch_label} machines for {region} prioritising cheapest total cost (based on {vcpus}vCPU+{ram_gb}GB), interleaving spot and on-demand"
        if max_daily_cost:
            description += f", max ${max_daily_cost}/day"
        output.write(f"  description: \"{description}\"\n")
        output.write("  whenUnsatisfiable: ScaleUpAnyway\n")
        output.write("  nodePoolAutoCreation:\n")
        output.write("    enabled: true\n")
        output.write("  priorities:\n")

        # Write all options sorted by total cost
        for opt in all_sorted:
            spot_str = "true" if opt['is_spot'] else "false"
            type_label = "spot" if opt['is_spot'] else "on-demand"
            output.write(f"  - machineFamily: {opt['family']}  # ${opt['total']*24:.2f}/day ({type_label}, {vcpus}vCPU+{ram_gb}GB)\n")
            output.write(f"    spot: {spot_str}\n")
    finally:
        if output_file is not None:
            output.close()

    if output_file:
        print(f"\n✓ Generated compute class spec: {output_file}", file=sys.stderr)
    else:
        print("\n✓ Generated compute class spec", file=sys.stderr)


def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Generate GKE ComputeClass spec with cost-optimised machine priorities using GCP Cloud Billing API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  %(prog)s europe-north1 > config.yaml                  # Output to file
  %(prog)s europe-north1 --max-cost 5 -o config.yaml   # Cap at $5/day, save to file
  %(prog)s europe-north1 --arch arm --max-cost 3        # ARM instances only
  %(prog)s us-central1 --max-cost 10 --vcpus 8 --ram 32 # Custom instance size
  %(prog)s europe-north1 2>/dev/null | kubectl apply -f - # Suppress logs, apply directly

Notes:
  - Requires Cloud Billing API enabled: gcloud services enable cloudbilling.googleapis.com
  - Requires authentication: gcloud auth application-default login
  - Use --arch to select AMD64 (default) or ARM instances
  - Interleaves spot and on-demand by total cost for optimal price/performance
        '''
    )

    parser.add_argument(
        'region',
        nargs='?',
        default='europe-north1',
        help='GCP region (default: europe-north1)'
    )

    parser.add_argument(
        '--max-cost',
        type=float,
        default=None,
        metavar='DOLLARS',
        help='Maximum daily cost in USD for a single instance (default: no limit)'
    )

    parser.add_argument(
        '--vcpus',
        type=int,
        default=4,
        metavar='N',
        help='Number of vCPUs for cost calculation (default: 4)'
    )

    parser.add_argument(
        '--ram',
        type=int,
        default=16,
        metavar='GB',
        help='RAM in GB for cost calculation (default: 16)'
    )

    parser.add_argument(
        '--arch',
        choices=['amd64', 'arm'],
        default='amd64',
        help='CPU architecture to include (default: amd64)'
    )

    parser.add_argument(
        '-o', '--output',
        default=None,
        metavar='FILE',
        help='Output YAML file (default: stdout)'
    )

    args = parser.parse_args()

    try:
        generate_compute_class(
            region=args.region,
            output_file=args.output,
            vcpus=args.vcpus,
            ram_gb=args.ram,
            max_daily_cost=args.max_cost,
            arch=args.arch
        )
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == '__main__':
    main()
