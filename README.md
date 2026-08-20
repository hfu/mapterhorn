<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://mapterhorn.github.io/.github/brand/screen/mapterhorn-logo-darkmode.png">
  <source media="(prefers-color-scheme: light)" srcset="https://mapterhorn.github.io/.github/brand/screen/mapterhorn-logo.png">
  <img alt="Logo" src="https://mapterhorn.github.io/.github/brand/screen/mapterhorn-logo.png">
</picture>

Public terrain tiles for interactive web map visualizations

> **⚠️ Fork Note**: This is a fork of [mapterhorn/mapterhorn](https://github.com/mapterhorn/mapterhorn), used here for two purposes: (1) **orthophoto/RGB workflows** (aerial/satellite imagery) instead of elevation data — see [`FORK_NOTES.md`](FORK_NOTES.md) for what's different there and why; and (2) a **national-scale elevation pipeline for Japan** (standard Terrarium encoding, same as upstream), built on GSI's 基盤地図情報 DEM data across six source product types with a seven-tier priority merge — see [`hfu/mapterhorn-japan-bridge`](https://github.com/hfu/mapterhorn-japan-bridge) for the docs/viewer, and that repo's own `DECISIONS.md` (D11 onward) for the pipeline decisions. For upstream's own version, see [https://github.com/mapterhorn/mapterhorn](https://github.com/mapterhorn/mapterhorn).

## Viewer

[https://mapterhorn.com/viewer](https://mapterhorn.com/viewer)

## Examples

[https://mapterhorn.com/examples](https://mapterhorn.com/examples)

## Migrate from AWS Elevation Tiles (Tilezen Joerd)

```diff
"hillshadeSource": {
    "type": "raster-dem",
-   "tiles": ["https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png"],
+   "tiles": ["https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"],
    "encoding": "terrarium",
-   "tileSize": 256,
+   "tileSize": 512,
}

```

## Contributing

[CONTRIBUTING.md](./CONTRIBUTING.md)

## License

Code: BSD-3, see [LICENSE](https://github.com/mapterhorn/mapterhorn/blob/main/LICENSE).

Terrain data: various open-data sources, for a full list see [https://mapterhorn.com/attribution](https://mapterhorn.com/attribution).
