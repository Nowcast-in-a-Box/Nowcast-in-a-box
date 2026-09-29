# NiB Data Adapter Demo v1

## Overview

This demo provides a simple and extensible **Data Adapter** for loading heterogeneous meteorological data from local storage.

The main design idea is:

```text
Hub-generated YAML
        ↓
NiB Data Adapter
        ↓
Select loader by source
        ↓
Source-specific Loader
        ↓
Common DataBundle
        ↓
Downstream NiB modules
```

The Data Adapter does **not** download data. Data download and data loading are treated as separate steps.

The current demo focuses on reliable local data access, configurable time/channel selection, source-specific decoding, and a common output interface.

---

## Current Status

The following satellite loaders have been implemented and tested through the common `data_adapter.py` entry point:

| Source | Instrument / Product | Status |
|---|---|---|
| GK2A | AMI L1B | Implemented and tested |
| Himawari-9 | AHI HSD | Implemented and tested |
| FY-4B | AGRI L1 | Implemented and tested |
| MTG | FCI L1c | Implemented and tested |
| CMA Radar | Composite reflectivity | Interface reserved; decoder pending |

The CMA radar loader is intentionally left as a placeholder until a real radar file is available to confirm the file format, variables, units, projection, and missing-value definition.

---

## Project Structure

```text
nib_data_loaders_minimal/
│
├── data_adapter.py
│
├── gk2a_loader_v2/
│   └── loader.py
│
├── himawari9_loader_v1_1/
│   └── loader.py
│
├── fy4b_agri_loader_v1/
│   └── loader.py
│
├── mtg_fci_loader_v1/
│   └── loader.py
│
├── cma_radar/
│   └── loader.py
│
├── requirements.txt
└── README.md
```

Each data source keeps its own loader because file naming, segmentation, compression, metadata, calibration, and native resolution are source-specific.

The common `data_adapter.py` provides a single entry point for NiB.

---

## Common YAML Configuration

The NiB Hub is expected to generate a YAML configuration such as:

```yaml
source: gk2a_ami

data_dir: "C:/data/gk2a"

initial_time: "2022-01-01T00:00:00Z"
history_steps: 1
interval_minutes: 10

channels:
  - sw038

# [west, south, east, north]
region:
  bbox: [100.0, 10.0, 145.0, 55.0]
```

Common fields:

- `source`: data source identifier
- `data_dir`: local raw-data directory
- `initial_time`: final input observation time
- `history_steps`: number of requested historical time steps
- `interval_minutes`: interval between requested time steps
- `channels`: requested channels
- `region.bbox`: requested spatial domain in `[west, south, east, north]`

The source-specific loader is selected automatically from the `source` field.

---

## Run the Data Adapter

From the project root:

```bash
python data_adapter.py --config <path_to_yaml>
```

Example:

```bash
python data_adapter.py --config gk2a_loader_v2/example_success.yaml
```

The main entry point performs the following steps:

```text
1. Read and validate YAML
2. Select the source-specific loader
3. Find requested local files
4. Decode the source data
5. Normalize the loader output
6. Return a common DataBundle
```

---

## Common DataBundle

The individual loaders may use different internal structures, but `data_adapter.py` converts their outputs to a common interface:

```python
bundle = {
    "source": "...",

    "request": {
        "data_dir": "...",
        "initial_time": "...",
        "history_steps": ...,
        "interval_minutes": ...,
        "channels": [...],
        "region": {...},
    },

    "data": {
        time: {
            channel: xarray.DataArray
        }
    },

    "source_files": {
        time: {
            channel: [...]
        }
    },

    "metadata": {...}
}
```

Downstream modules can therefore use a common access pattern:

```python
data = bundle["data"][time][channel]
```

without knowing the original satellite file format.

---

## Design Boundary

Demo v1 standardizes the **data access interface**, not every physical detail of the raw data.

### Standardized in Demo v1

- YAML configuration
- Source selection
- Local file discovery
- Requested time/history selection
- Channel selection
- Source-specific decoding
- Error reporting
- Common DataBundle structure

### Kept source-specific in Demo v1

- Native spatial resolution
- Native projection
- Physical units
- Calibration details
- File segmentation and compression

This keeps the first demo lightweight while preserving the information needed by later processing modules.

---

## Region Handling

`region.bbox` is currently:

- validated;
- recorded in the request;
- reported by the loader.

**Actual spatial cropping is not performed in Demo v1.**

Spatial subsetting can be added later, preferably before large full-disk arrays are fully materialized when supported by the source format.

---

## Error Handling

The loaders are designed to fail clearly when requested data are unavailable or invalid.

Typical cases include:

- data directory not found;
- requested time not found;
- requested channel not found;
- incomplete source files;
- invalid configuration;
- multiple files matching one request.

The error message should include the requested time, channel, local path, or matching pattern whenever possible.

---

## Installation

A separate Python environment is recommended:

```bash
conda create -n nib-loader python=3.10 -y
conda activate nib-loader
pip install -r requirements.txt
```

Main dependencies include:

- Satpy
- xarray
- dask
- netCDF4
- h5py / hdf5plugin
- pyproj
- pyresample
- PyYAML

Some source formats may require additional native decoding libraries.

---

## Demo v1 Scope

The current demo is intentionally focused on:

> **One YAML configuration → automatic loader selection → ready-to-use data**

The following functions are outside the current Demo v1 scope:

- automatic data download;
- account / API-key management;
- actual spatial cropping;
- common-grid regridding;
- unit harmonization across all sources;
- model-specific normalization or tensor formatting;
- multi-source temporal alignment;
- missing-data interpolation;
- persistent cache / Zarr workflow.

These functions can be added later without changing the source-loader interface.

---

## Responsibility Boundary

The recommended separation is:

```text
Hub
  decides what data are requested
        ↓
Data Adapter
  handles data-source differences
        ↓
Model Adapter
  handles model-specific input requirements
        ↓
Model / Evaluation / Visualization
```

In short:

- **Hub:** what the user wants
- **Data Adapter:** how the source data are found and decoded
- **Model Adapter:** how the loaded data are transformed for a specific model

This separation keeps NiB modular and makes new data sources easier to add.
