# Japan Terrain Tiles — Mapterhorn Bridge (Interim)

**Status: interim bridge product, not an official Mapterhorn distribution
channel.** [Mapterhorn](https://mapterhorn.com/) is the upstream project
this data is formatted for and modeled after; this product exists only
because upstream's own Japan 1m elevation source (`jpdem1a`) hasn't yet
picked up a July 2026 GSI survey update for Hokkaido. Once it does, this
product should be considered retired — check
[mapterhorn.com](https://mapterhorn.com/) for the official tiles first.

## What's in this dataset

Terrain tiles in Mapterhorn's own format: PMTiles archives, Terrarium
encoding, WebP tiles, 512×512px, generated with
[`hfu/mapterhorn`](https://github.com/hfu/mapterhorn) (a fork of
`mapterhorn/mapterhorn` carrying a handful of generic upstream bug fixes,
see that repo's `FORK_NOTES.md`) from freshly-reprocessed GSI 1m DEM
data published at
[`smartmaps/japan-geotiff-dem`](https://source.coop/smartmaps/japan-geotiff-dem).

Files follow Mapterhorn's own bundle naming convention: `{z}-{x}-{y}.pmtiles`,
where z/x/y identify the zoom-6 tile each bundle covers. Coverage is
partial and grows incrementally as more of Hokkaido is reprocessed --
this file won't always describe the current extent; browse this
product's file listing for what's actually present.

**Encoding**: `elevation = (R × 256 + G + B / 256) - 32768` (meters).

## Data source, license, and attribution

Source elevation data: Geospatial Information Authority of Japan
(国土地理院), via `smartmaps/japan-geotiff-dem`.

測量法に基づく国土地理院長承認（複製）R8JHf51

不特定多数の者が提供を受けることができる状態に置く措置をとるために本製品を複製する場合には、国土地理院の長の承認を得なければなりません。

Approval for Reproduction pursuant to the Survey Act, granted by the
Director-General of the Geospatial Information Authority of Japan
(R8JHf51).

This product's own packaging (tiling, encoding, bundling) is CC0-1.0;
that does **not** waive GSI's own attribution requirement on the
underlying elevation data, which is a condition of GSI's data terms,
not this project's choice.

## Feedback / issues

File an issue at <https://github.com/hfu/mapterhorn/issues>, or see
that repository for the pipeline code.
