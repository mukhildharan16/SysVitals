-- SysVitals Supabase Telemetry Automatic Cleanup Migration Script
-- Run this in your Supabase SQL Editor.

-- 1. Enable the pg_cron extension
create extension if not exists pg_cron;

-- 2. Create index on the ts timestamp column to optimize delete operations
create index if not exists idx_telemetry_ts
on telemetry(ts);

-- 3. Create database function to delete telemetry older than 10 minutes
create or replace function delete_old_telemetry()
returns void
language plpgsql
security definer -- runs with owner privileges to bypass RLS policies
as $$
begin
    delete from telemetry
    where ts < now() - interval '10 minutes';
end;
$$;

-- 4. Schedule the cleanup job to run every minute
-- First, unschedule any existing job of the same name to prevent duplicates
select cron.unschedule('telemetry-retention-cleanup');

-- Schedule new job
select cron.schedule(
    'telemetry-retention-cleanup', -- unique name of the cron job
    '*/1 * * * *',                 -- standard cron syntax for "every minute"
    'select delete_old_telemetry();'
);
