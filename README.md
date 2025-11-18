# gkecc

[![Tests](https://github.com/brtkwr/gkecc/actions/workflows/test.yml/badge.svg)](https://github.com/brtkwr/gkecc/actions/workflows/test.yml)

Generate cost-optimised GKE ComputeClass specs from live GCP pricing data.

## What it does

`gkecc` fetches real-time pricing from Google Cloud's Billing API and generates a GKE ComputeClass manifest that prioritises the cheapest instance types. It intelligently interleaves spot and on-demand instances based on total cost (CPU + RAM), ensuring you get the best price-per-performance whilst maintaining availability.

## Features

- 🔄 **Live pricing** - Fetches current spot and on-demand prices from GCP API
- 💰 **Cost-optimised** - Sorts by total cost (cores + RAM) not just core price
- 🎯 **Smart interleaving** - Mixes spot and on-demand when on-demand is actually cheaper
- 🏗️ **Architecture support** - Filter for AMD64 or ARM instances
- 📊 **Configurable** - Set max daily cost, vCPU/RAM ratios, output format
- 🔌 **Unix-friendly** - Outputs to stdout by default for easy piping

## Installation

```bash
# Using uv (recommended)
git clone https://github.com/brtkwr/gkecc.git
cd gkecc
uv sync
uv run gkecc --help

# Or install globally
uv tool install .
```

## Prerequisites

1. **Enable Cloud Billing API:**
   ```bash
   gcloud services enable cloudbilling.googleapis.com
   ```

2. **Authenticate:**
   ```bash
   gcloud auth application-default login
   ```

## Usage

```bash
# Basic usage - output to stdout
gkecc europe-north1

# Save to file
gkecc europe-north1 > compute-class.yaml

# Or use -o flag
gkecc europe-north1 -o compute-class.yaml

# Cap at $5/day per instance (4vCPU + 16GB)
gkecc europe-north1 --max-cost 5

# ARM instances only
gkecc europe-north1 --arch arm --max-cost 3

# Custom instance size (8 vCPU + 32GB RAM)
gkecc us-central1 --max-cost 10 --vcpus 8 --ram 32

# Add node labels (comma-separated or multiple flags)
gkecc europe-north1 --node-label workload=core,env=production
gkecc europe-north1 --node-label workload=core --node-label env=production

# Suppress logs and apply directly
gkecc europe-north1 2>/dev/null | kubectl apply -f -
```

## How it works

1. **Fetches pricing** - Queries GCP Billing API for all instance families in your region
2. **Calculates total cost** - Combines core and RAM pricing for realistic cost comparison
3. **Sorts by price** - Orders all options (spot and on-demand) by total cost
4. **Filters** - Excludes ARM instances (by default) and applies cost limits
5. **Generates YAML** - Outputs a GKE ComputeClass manifest with priorities

### Why total cost matters

Many tools only consider core pricing, but that's misleading:
- **e2** (cost-optimised): $0.00527/core/hr, $0.00071/GB/hr
- **c2d** (compute-optimised): $0.00670/core/hr, $0.00090/GB/hr

If you only looked at core pricing, c2d looks 27% more expensive. But for a 4 vCPU + 16GB instance:
- **e2 spot**: $0.78/day
- **c2d spot**: $0.99/day ← 1.3x more expensive!

The RAM pricing amplifies the difference.

`gkecc` catches this by calculating total cost, not just core cost.

## Example output

```yaml
apiVersion: cloud.google.com/v1
kind: ComputeClass
metadata:
  name: cost-optimised-europe-north1
spec:
  description: "Cost-optimised AMD64 for europe-north1"
  whenUnsatisfiable: ScaleUpAnyway
  nodePoolAutoCreation:
    enabled: true
    nodeLabels:
      workload: "core"
      env: "production"
  priorities:
  - machineFamily: t2d  # $0.46/day (spot, 4vCPU+16GB, cheapest)
    spot: true
  - machineFamily: m3  # $0.53/day (spot, 4vCPU+16GB, 1.1x)
    spot: true
  # ... more spot instances ...
  - machineFamily: n2d  # $2.62/day (on-demand, 4vCPU+16GB, 5.7x)
    spot: false
  - machineFamily: g2  # $2.72/day (on-demand, 4vCPU+16GB, 5.9x)
    spot: false
  # ... more on-demand as fallbacks ...
```

## Options

```
usage: gkecc [-h] [--max-cost DOLLARS] [--vcpus N] [--ram GB]
             [--arch {amd64,arm}] [--node-label KEY=VALUE] [-o FILE]
             [region]

positional arguments:
  region                 GCP region (default: europe-north1)

options:
  --max-cost DOLLARS     Maximum daily cost in USD (default: no limit)
  --vcpus N              Number of vCPUs for cost calculation (default: 4)
  --ram GB               RAM in GB for cost calculation (default: 16)
  --arch {amd64,arm}     CPU architecture to include (default: amd64)
  --node-label KEY=VALUE Node labels to apply (can be specified multiple times or comma-separated)
  --refresh              Refresh pricing cache from API (ignore cached data)
  --verbose              Show debug logs
  --format {table,computeclass}  Output format (default: computeclass)
  -o, --output FILE      Output YAML file (default: stdout)
```

## Why interleaving matters

Traditional approach: "All spot first, then all on-demand"
- ❌ Some spot instances are more expensive than some on-demand options
- ❌ You might miss cheaper on-demand alternatives

**gkecc approach**: "Cheapest first, regardless of type"
- ✅ n2d on-demand ($2.62/day) comes before z3 spot ($2.74/day)
- ✅ Optimal cost-per-availability ratio

## License

MIT

## Contributing

PRs welcome! This tool is designed to be simple and focused on cost optimisation.
