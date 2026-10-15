-- WebAPI デモ用スキーマ
-- public スキーマを避け、Supabase の Data API（PostgREST）に露出させない
create schema if not exists api_demo;
set search_path = api_demo, public;

-- UUID v7（時系列ソート可能）。PostgreSQL 18 未満には組み込み関数がないため自前で定義する
create or replace function api_demo.uuid_generate_v7(ts timestamptz default clock_timestamp())
returns uuid language plpgsql volatile as $$
declare
  unix_ts_ms bytea;
  uuid_bytes bytea;
begin
  unix_ts_ms := substring(int8send(floor(extract(epoch from ts) * 1000)::bigint) from 3);
  uuid_bytes := uuid_send(gen_random_uuid());
  uuid_bytes := overlay(uuid_bytes placing unix_ts_ms from 1 for 6);
  uuid_bytes := set_byte(uuid_bytes, 6, (b'0111' || get_byte(uuid_bytes, 6)::bit(4))::bit(8)::int);
  return encode(uuid_bytes, 'hex')::uuid;
end $$;

create table if not exists profiles (
  id           uuid primary key,              -- Supabase Auth の user id（sub）
  email        text not null unique,
  display_name text not null,
  role         text not null default 'member' check (role in ('member', 'admin')),
  created_at   timestamptz not null default now()
);

create table if not exists orders (
  id         uuid primary key default api_demo.uuid_generate_v7(),
  order_no   bigint generated always as identity unique,  -- 画面表示用の連番（外部 ID には使わない）
  user_id    uuid not null,
  status     text not null check (status in ('pending', 'paid', 'shipped', 'cancelled')),
  items      jsonb not null,
  total_yen  integer not null check (total_yen >= 0),
  note       text,
  source     text not null default 'api',  -- seed / api（make reset で api 由来だけを消す）
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ESR ルール：Equality(user_id, status) → Sort(created_at, id) → Range(created_at)
create index if not exists orders_user_status_created_idx on orders (user_id, status, created_at desc, id desc);
create index if not exists orders_user_created_idx on orders (user_id, created_at desc, id desc);

create table if not exists idempotency_keys (
  user_id          uuid not null,
  key              text not null,
  method           text not null,
  path             text not null,
  request_hash     text not null,
  status           text not null check (status in ('started', 'finished')),
  response_status  integer,
  response_body    jsonb,
  response_headers jsonb,
  created_at       timestamptz not null default now(),
  primary key (user_id, key)
);

create table if not exists outbox (
  id             uuid primary key default api_demo.uuid_generate_v7(),
  aggregate_type text not null,
  aggregate_id   uuid not null,
  event_type     text not null,
  payload        jsonb not null,
  created_at     timestamptz not null default now(),
  published_at   timestamptz
);
create index if not exists outbox_unpublished_idx on outbox (created_at) where published_at is null;
