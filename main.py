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


# ANSI color codes
class Colors:
    GREY = '\033[90m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'


def log(msg, color=Colors.GREY):
    """Print colored message to stderr"""
    print(f"{color}{msg}{Colors.RESET}", file=sys.stderr)


# ARM-based machine families
ARM_FAMILIES = {'t2a', 'c4a'}
# AMD64-based machine families (all others are AMD64)
AMD64_FAMILIES = None  # None means "not in ARM_FAMILIES"


def get_cache_dir():
    """Get cache directory path"""
    cache_dir = Path.home() / '.cache' / 'gkecc'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def load_sku_cache():
    """Load cached SKU data if it's from today"""
    cache_file = get_cache_dir() / 'skus.json'
    if cache_file.exists():
        try:
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)

                # Check if cache is from today
                cached_date = cache_data.get('date')
                today = datetime.now().strftime('%Y-%m-%d')

                if cached_date == today:
                    log(f"✓ Loaded SKU data from cache ({cached_date})", Colors.GREEN)
                    return cache_data['skus']
                else:
                    log(f"Cache is stale (from {cached_date}), fetching fresh data", Colors.YELLOW)
                    return None
        except Exception as e:
            log(f"Failed to load SKU cache: {e}", Colors.YELLOW)
    return None


def save_sku_cache(sku_data):
    """Save SKU data to cache with today's date"""
    cache_file = get_cache_dir() / 'skus.json'
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        cache_data = {
            'date': today,
            'skus': sku_data
        }
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        log(f"✓ Saved SKU data to cache ({today})", Colors.GREEN)
    except Exception as e:
        log(f"Failed to save SKU cache: {e}", Colors.YELLOW)


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


def parse_pricing_data(region='europe-north1', arch='amd64', use_cache=True):
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
            if 'Compute Engine' in service.display_name:
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
                sku_data_list.append({
                    'description': sku.description,
                    'regions': list(sku.service_regions),
                    'price': price
                })

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
        desc = sku_data['description'].lower()

        # Filter for region
        if not any(r.lower() == region.lower() for r in sku_data['regions']):
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

        price = sku_data['price']
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
        if (family in spot_core_prices and family in spot_ram_prices and
            family in ondemand_core_prices and family in ondemand_ram_prices):

            pricing[family] = {
                'spot_core': sum(spot_core_prices[family]) / len(spot_core_prices[family]),
                'spot_ram': sum(spot_ram_prices[family]) / len(spot_ram_prices[family]),
                'ondemand_core': sum(ondemand_core_prices[family]) / len(ondemand_core_prices[family]),
                'ondemand_ram': sum(ondemand_ram_prices[family]) / len(ondemand_ram_prices[family]),
            }

    return pricing


def generate_compute_class(region='europe-north1', output_file=None, vcpus=4, ram_gb=16, max_daily_cost=None, arch='amd64', use_cache=True):
    """Generate compute class spec from API pricing"""

    pricing = parse_pricing_data(region, arch=arch, use_cache=use_cache)

    if not pricing:
        log("No pricing data found!", Colors.YELLOW)
        return

    log(f"\nFound {len(pricing)} machine families with complete pricing", Colors.GREEN)

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
        log(f"\nFiltered to options under ${max_daily_cost}/day", Colors.YELLOW)

    log(f"\nAll options sorted by total cost for {vcpus} vCPU + {ram_gb}GB RAM (per day, USD):", Colors.BLUE)
    for opt in all_sorted:
        spot_label = "spot" if opt['is_spot'] else "on-demand"
        log(f"  {opt['family']:10} {spot_label:10} ${opt['total']*24:.2f}/day  (${opt['total']:.5f}/hr)", Colors.GREY)

    # Generate YAML
    output = sys.stdout if output_file is None else open(output_file, 'w')

    try:
        output.write("apiVersion: cloud.google.com/v1\n")
        output.write("kind: ComputeClass\n")
        output.write("metadata:\n")
        output.write(f"  name: cost-optimised-{region}\n")
        output.write("spec:\n")
        arch_label = arch.upper()
        description = f"Cost-optimised {arch_label} for {region} (based on {vcpus}vCPU+{ram_gb}GB)"
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
        log(f"\n✓ Generated compute class spec: {output_file}", Colors.GREEN)
    else:
        log("\n✓ Generated compute class spec", Colors.GREEN)


def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Generate GKE ComputeClass spec with cost-optimised machine priorities using GCP Cloud Billing API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
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
        '--refresh',
        action='store_true',
        help='Refresh pricing cache from API (ignore cached data)'
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
            arch=args.arch,
            use_cache=not args.refresh
        )
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)


if __name__ == '__main__':
    main()
