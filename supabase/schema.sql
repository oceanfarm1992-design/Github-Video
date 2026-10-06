-- Run once in Supabase: SQL Editor -> New query -> paste -> Run.
-- Holds comment events (personal data) privately; never store these in the public repo.

create table if not exists cta_links (
  id          bigserial primary key,
  platform    text not null,
  post_id     text not null,
  keyword     text not null,            -- exactly one of GITHUB/TOOL/CODE/DOCS/DEMO/SOURCE
  resource_url text not null,
  response    text not null,            -- approved reply text (the only thing ever sent)
  created_at  timestamptz not null default now(),
  unique (platform, post_id)
);

create table if not exists comment_events (
  id          bigserial primary key,
  platform    text not null,
  post_id     text not null,
  comment_id  text not null,
  author_id   text,
  author_name text,
  body        text,
  keyword     text,
  status      text not null default 'pending',   -- pending | replied | failed | skipped
  error       text,
  created_at  timestamptz not null default now(),
  replied_at  timestamptz,
  unique (platform, comment_id)                   -- idempotency: one event per comment
);
create index if not exists comment_events_post on comment_events (platform, post_id, author_id);
create index if not exists comment_events_status on comment_events (status);

-- Reply log columns (added later; safe to re-run)
alter table comment_events add column if not exists kind text;        -- keyword | ai
alter table comment_events add column if not exists reply_text text;  -- exactly what was posted

-- Lock the tables: only the service-role key (used by the pipeline) can access them.
alter table cta_links enable row level security;
alter table comment_events enable row level security;
