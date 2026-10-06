# PostGIS

Patterns for location features (store and venue finders, delivery or service areas, map views) and the privacy rules that come with them.

## Setup

```python
# Alembic migration (needs a role allowed to create extensions; managed providers support PostGIS)
op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
```

Use the same PostgreSQL and PostGIS major versions locally (`postgis/postgis` Docker image), in CI, and in production. Confirm your managed provider offers the PostGIS version you need before committing to it.

Add PostGIS when a requirement needs spatial queries. A city or postcode filter doesn't.

## Choosing a type

| Need | Type |
|---|---|
| Points anywhere on Earth, distances and radii in meters | `geography(Point, 4326)` |
| Areas such as delivery zones or service regions, "is this point inside?" | `geography(Polygon, 4326)`, or `geometry` in a local projected SRID for heavy polygon work |
| Heavy spatial analysis (unions, buffers, area) within one region | `geometry(<type>, <local projected SRID>)` |
| Rendering map tiles | `geometry(..., 3857)`, derived at query time or stored separately |

For most product features, `geography(Point, 4326)` is the default: `ST_DWithin` and `ST_Distance` work in meters with no projection math.

## SQLAlchemy with GeoAlchemy2

```python
from geoalchemy2 import Geography, WKBElement

class Store(Base):
    __tablename__ = "stores"
    location: Mapped[WKBElement] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),  # index declared explicitly below
    )
    __table_args__ = (
        Index("ix_stores_location_open", "location", postgresql_using="gist",
              postgresql_where=text("status = 'open'")),
    )
```

By default GeoAlchemy2 creates a GiST index for every spatial column. Turn that off with `spatial_index=False` and declare the index yourself. Otherwise you can't make it partial, and Alembic autogenerate may emit a duplicate.

## Writing points

```python
from geoalchemy2 import Geography
from sqlalchemy import cast, func

def point(lat: float, lng: float):
    # x = longitude, y = latitude. Getting this backwards is the most common geo bug.
    return cast(func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326), Geography(srid=4326))
```

Validate ranges at the API boundary (`lat ∈ [-90, 90]`, `lng ∈ [-180, 180]`) with Pydantic `Field(ge=..., le=...)`. Never build WKT strings with f-strings. Pass coordinates as bound parameters.

## Query patterns

**Radius search (uses the GiST index):**

```sql
SELECT id, name, ST_Distance(location, $p) AS meters
FROM stores
WHERE status = 'open'
  AND ST_DWithin(location, $p, $radius_m)   -- $p = ST_SetSRID(ST_MakePoint($lng, $lat), 4326)::geography
ORDER BY meters
LIMIT 50;
```

- Never filter with `ST_Distance(location, $p) < $r`. It computes the distance for every row and can't use the index.
- Compute `ST_Distance` only for the rows you return, never as a filter.

**Nearest N (KNN):**

```sql
SELECT id, name, ST_Distance(location, $p) AS meters
FROM stores
WHERE status = 'open'
ORDER BY location <-> $p
LIMIT 10;
```

**Point in area** (does this address fall inside a delivery zone?):

```sql
SELECT id, fee_cents
FROM delivery_zones
WHERE ST_Covers(area, $p)          -- area: geography(Polygon, 4326) with a GiST index
ORDER BY priority
LIMIT 1;
```

**Map viewport:**

```sql
SELECT id, location
FROM stores
WHERE status = 'open'
  AND ST_Intersects(location, ST_MakeEnvelope($min_lng, $min_lat, $max_lng, $max_lat, 4326)::geography)
LIMIT 500;
```

Clamp the viewport size and cap the result count. At low zoom levels, return server-side clusters (grid aggregation with `ST_SnapToGrid`, or vector tiles via `ST_AsMVT`) instead of thousands of raw points.

**Combining distance with another sort or pagination:** keyset pagination needs a stable sort key. A distance relative to a query point changes with every request. Either sort by a stored column (`created_at, id`) inside a radius, or paginate by distance with the query point fixed in the cursor. Check both plans with `EXPLAIN ANALYZE` on production-like data density.

**Scaling hot radius queries:** when dense areas make radius queries expensive, add a precomputed cell column (geohash or H3) with a btree on `(cell, <sort columns>)`. Query the cells that cover the radius, then apply `ST_DWithin` as an exact filter on the candidates.

## Location privacy (non-negotiable)

A person's precise location is sensitive personal data. These rules apply whenever a feature can reveal where a **person** is or was (couriers, customers, users). They don't apply to public places such as stores and venues:

1. **Never return another person's stored coordinates.** Return a coarse location, such as the center of a grid cell (`ST_SnapToGrid(location::geometry, 0.005)`, roughly 550 m of latitude) or an H3 cell at a coarse resolution.
2. **Bucket distances** ("< 1 km", "1–2 km", "2–5 km"), and compute them from the coarse location, not the precise one.
3. **Defend against trilateration.** If the API returns exact distances from arbitrary query points, anyone can locate a person by querying from three points. Coarse buckets, distances computed from snapped points, and rate limits on query-point changes make this impractical.
4. **Minimize retention.** Store a person's current location as a single overwritten value, not a history, unless a feature needs history and the privacy policy says so.
5. **Strip EXIF GPS metadata** from uploaded photos before they are stored or served.
6. Location permission prompts and privacy labels must match what you actually collect (see the mobile skills).

## Testing

Use fixed reference coordinates with known distances, so a lat/lng swap fails loudly:

```python
BIG_BEN = (51.5007, -0.1246)          # (lat, lng)
TOWER_OF_LONDON = (51.5081, -0.0759)  # ≈ 3.5 km from Big Ben

# A 4 km radius from Big Ben must include the Tower of London.
# With lat/lng swapped, the points land near the equator ≈ 5.4 km apart, and the test fails.
```

Also test:

- Points exactly at the radius boundary.
- Points just inside and just outside a zone polygon.
- That responses about people contain coarse coordinates only, never the stored point.
