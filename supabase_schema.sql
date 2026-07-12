-- SysVitals Supabase Database Initialization Schema
-- Run this in your Supabase SQL Editor to create the required tables and indexes.

-- Enable pgcrypto extension for UUIDs and random byte generation
create extension if not exists pgcrypto;

-- 1. Users table (Custom auth with bcrypt hashed passwords)
create table if not exists users (
    id uuid primary key default gen_random_uuid(),
    username text unique not null,
    password_hash text not null,
    created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- 2. Devices table (Each user can own multiple devices with auto-generated secrets)
create table if not exists devices (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references users(id) on delete cascade not null,
    name text not null,
    hostname text,
    device_secret text unique not null default 'sv_' || encode(gen_random_bytes(24), 'hex'),
    created_at timestamp with time zone default timezone('utc'::text, now()) not null,
    last_seen timestamp with time zone
);

-- 3. Telemetry table (Stores raw telemetry records linked to device_id)
create table if not exists telemetry (
    id bigserial primary key,
    device_id uuid references devices(id) on delete cascade not null,
    ts timestamp with time zone default timezone('utc'::text, now()) not null,
    cpu_temp double precision,
    cpu_power double precision,
    cpu_clock double precision,
    cpu_util double precision,
    gpu_name text,
    gpu_temp double precision,
    gpu_power double precision,
    gpu_util double precision,
    gpu_mem_used double precision,
    gpu_mem_total double precision,
    gpu_active boolean,
    ac_plugged boolean,
    battery_power double precision,
    battery_voltage double precision,
    battery_level double precision,
    power_mode text
);

-- Indexes for querying performance
create index if not exists idx_telemetry_device_ts on telemetry(device_id, ts desc);
create index if not exists idx_devices_secret on devices(device_secret);
create index if not exists idx_devices_user on devices(user_id);
