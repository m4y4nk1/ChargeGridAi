-- osm2pgsql flex output style for ChargeGrid AI (Phase 1 foundation import).
--
-- Produces road_segment, poi, grid_asset, and admin_boundary per Section 8.2
-- of the project brief, directly from OSM tags. This is a single-source
-- import only — deduping/fusing against OCM, government registries, and
-- Google lands in Phase 2 (Section 7.3). Every row is tagged
-- source_id = 'osm' / license_class = 'OPEN' so downstream fusion and the
-- frontend license guard (ADR 0002) can treat it correctly from day one.

local srid = 4326

local road_segment = osm2pgsql.define_way_table('road_segment', {
    { column = 'osm_id',    type = 'bigint' },
    { column = 'class',     type = 'text' },
    { column = 'name',      type = 'text' },
    { column = 'ref',       type = 'text' },
    { column = 'maxspeed',  type = 'text' },
    { column = 'lanes',     type = 'int' },
    { column = 'geom',      type = 'linestring', projection = srid },
})

local poi = osm2pgsql.define_table({
    name = 'poi',
    ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
    columns = {
        { column = 'category',      type = 'text' },
        { column = 'name',          type = 'text' },
        { column = 'source_id',     type = 'text' },
        { column = 'license_class', type = 'text' },
        { column = 'attrs',         type = 'jsonb' },
        { column = 'geom',          type = 'point', projection = srid },
    },
})

local grid_asset = osm2pgsql.define_table({
    name = 'grid_asset',
    ids = { type = 'any', id_column = 'osm_id', type_column = 'osm_type' },
    columns = {
        { column = 'type',       type = 'text' },
        { column = 'voltage_kv', type = 'real' },
        { column = 'source_id',  type = 'text' },
        { column = 'confidence', type = 'text' },
        { column = 'geom',       type = 'geometry', projection = srid },
    },
})

local admin_boundary = osm2pgsql.define_table({
    name = 'admin_boundary',
    ids = { type = 'relation', id_column = 'osm_id' },
    columns = {
        { column = 'level',       type = 'text' },
        { column = 'name',        type = 'text' },
        { column = 'code',        type = 'text' },
        { column = 'admin_level', type = 'int' },
        { column = 'geom',        type = 'multipolygon', projection = srid },
    },
})

-- Land cover and land use polygons for site feasibility (Section 9.5):
-- `restricted` classes block sites outright (rule F01); `landuse` classes
-- give context for host compatibility (rule F05).
local landcover = osm2pgsql.define_table({
    name = 'landcover',
    ids = { type = 'area', id_column = 'area_id' },
    columns = {
        { column = 'class',      type = 'text' },  -- water | forest | military | protected | aerodrome | residential | commercial | industrial
        { column = 'restricted', type = 'boolean' },
        { column = 'tag',        type = 'text' },  -- the OSM key=value that matched
        { column = 'name',       type = 'text' },
        { column = 'geom',       type = 'geometry', projection = srid },
    },
})

local function landcover_class(tags)
    if tags.natural == 'water' or tags.water or tags.landuse == 'reservoir' or tags.landuse == 'basin' then
        return 'water', true, 'water'
    end
    if tags.natural == 'wetland' then return 'water', true, 'natural=wetland' end
    if tags.natural == 'wood' or tags.landuse == 'forest' then return 'forest', true, 'forest' end
    if tags.landuse == 'military' or tags.military then return 'military', true, 'military' end
    if tags.leisure == 'nature_reserve' or tags.boundary == 'protected_area' or tags.boundary == 'national_park' then
        return 'protected', true, 'protected'
    end
    if tags.aeroway == 'aerodrome' then return 'aerodrome', true, 'aeroway=aerodrome' end
    if tags.landuse == 'residential' then return 'residential', false, 'landuse=residential' end
    if tags.landuse == 'commercial' or tags.landuse == 'retail' then return 'commercial', false, 'landuse=' .. tags.landuse end
    if tags.landuse == 'industrial' then return 'industrial', false, 'landuse=industrial' end
    return nil
end

-- config/poi_categories.yaml (Section 7.2) is the long-term source of truth
-- for OSM-tag -> category mapping; this table mirrors it for the subset of
-- tags actually present in the Phase 1 import.
local poi_tag_categories = {
    amenity = {
        charging_station = 'EV_CHARGER',
        fuel = 'FUEL',
        parking = 'PARKING',
        restaurant = 'RESTAURANT',
        fast_food = 'RESTAURANT',
        hospital = 'HOSPITAL',
        clinic = 'HOSPITAL',
    },
    shop = {
        mall = 'MALL',
        supermarket = 'SUPERMARKET',
    },
    tourism = {
        hotel = 'HOTEL',
        motel = 'HOTEL',
    },
    landuse = {
        industrial = 'INDUSTRIAL',
    },
    office = {
        -- any office=* value counts as an office park POI for our purposes
    },
    railway = {
        station = 'TRANSIT_HUB',
    },
    highway = {
        bus_stop = 'TRANSIT_HUB',
    },
}

local function poi_category(tags)
    for key, mapping in pairs(poi_tag_categories) do
        local value = tags[key]
        if value then
            local category = mapping[value]
            if category then
                return category
            end
        end
    end
    if tags.office then
        return 'OFFICE_PARK'
    end
    return nil
end

-- Drivable road classes. `service` (campus roads, driveways, fuel-station
-- forecourts, parking aisles) and `living_street` matter for site access:
-- without them, malls and office parks look unreachable (feasibility F02).
local road_classes = {
    motorway = true, trunk = true, primary = true, secondary = true,
    tertiary = true, residential = true, unclassified = true,
    service = true, living_street = true,
    motorway_link = true, trunk_link = true, primary_link = true,
    secondary_link = true, tertiary_link = true,
}

function osm2pgsql.process_node(object)
    local tags = object.tags

    if tags.highway == 'bus_stop' or poi_category(tags) then
        local category = poi_category(tags)
        if category then
            poi:insert({
                category = category,
                name = tags.name,
                source_id = 'osm',
                license_class = 'OPEN',
                attrs = tags,
                geom = object:as_point(),
            })
        end
    end

    if tags.power == 'substation' or tags.power == 'transformer' or tags.power == 'tower' then
        grid_asset:insert({
            type = tags.power,
            voltage_kv = tonumber(tags.voltage) and tonumber(tags.voltage) / 1000 or nil,
            source_id = 'osm',
            confidence = 'LOW',
            geom = object:as_point(),
        })
    end
end

function osm2pgsql.process_way(object)
    local tags = object.tags

    if tags.highway and road_classes[tags.highway] then
        road_segment:insert({
            osm_id = object.id,
            class = tags.highway,
            name = tags.name,
            ref = tags.ref,
            maxspeed = tags.maxspeed,
            lanes = tonumber(tags.lanes),
            geom = object:as_linestring(),
        })
    end

    if tags.power == 'line' or tags.power == 'minor_line' then
        grid_asset:insert({
            type = 'line',
            voltage_kv = tonumber(tags.voltage) and tonumber(tags.voltage) / 1000 or nil,
            source_id = 'osm',
            confidence = 'LOW',
            geom = object:as_linestring(),
        })
    end
    if tags.power == 'substation' then
        grid_asset:insert({
            type = 'substation',
            voltage_kv = tonumber(tags.voltage) and tonumber(tags.voltage) / 1000 or nil,
            source_id = 'osm',
            confidence = 'LOW',
            geom = object.is_closed and object:as_polygon() or object:as_linestring(),
        })
    end

    if object.is_closed then
        local class, restricted, tag = landcover_class(tags)
        if class then
            landcover:insert({
                class = class, restricted = restricted, tag = tag, name = tags.name,
                geom = object:as_polygon(),
            })
        end
    end

    -- Way-tagged POIs (e.g. malls mapped as building outlines) — use the
    -- polygon centroid so `poi` stays a point table throughout.
    if object.is_closed then
        local category = poi_category(tags)
        if category then
            poi:insert({
                category = category,
                name = tags.name,
                source_id = 'osm',
                license_class = 'OPEN',
                attrs = tags,
                geom = object:as_polygon():centroid(),
            })
        end
    end
end

-- Indian admin_level convention (approximate; refine once Census/PMC boundary
-- data is cross-checked in Phase 2): 4=state, 6=district, 7=taluka/tehsil,
-- 8 and above=city/ward level.
local function admin_level_to_our_level(admin_level)
    if admin_level == 4 then return 'state' end
    if admin_level == 6 then return 'district' end
    if admin_level == 7 then return 'taluka' end
    if admin_level and admin_level >= 8 then return 'ward' end
    return 'other'
end

function osm2pgsql.process_relation(object)
    local tags = object.tags

    if tags.type == 'multipolygon' or tags.boundary == 'protected_area' or tags.boundary == 'national_park' then
        local class, restricted, tag = landcover_class(tags)
        if class then
            landcover:insert({
                class = class, restricted = restricted, tag = tag, name = tags.name,
                geom = object:as_multipolygon(),
            })
        end
    end

    if tags.boundary == 'administrative' and (tags.type == 'boundary' or tags.type == 'multipolygon') then
        admin_boundary:insert({
            level = admin_level_to_our_level(tonumber(tags.admin_level)),
            name = tags.name,
            code = tags['ISO3166-2'] or tags.ref,
            admin_level = tonumber(tags.admin_level),
            geom = object:as_multipolygon(),
        })
    end
end
