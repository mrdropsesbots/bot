create table categories (
  id serial primary key,
  name text not null,
  icon text,
  sort_order int default 0
);
insert into categories (name, icon, sort_order) values
  ('Одежда','👕',1),('Обувь','👟',2),('Техника','📱',3),
  ('Мебель','🪑',4),('Детское','🧸',5),('Спорт','⚽',6),
  ('Книги','📚',7),('Для дома','🏠',8),('Авто','🚗',9),('Другое','📦',10);

create table profiles (
  id uuid primary key default gen_random_uuid(),
  telegram_id bigint unique not null,
  username text,
  full_name text,
  phone text,
  city text default 'Минск',
  created_at timestamp default now()
);

create table items (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid references profiles(id) on delete cascade,
  category_id int references categories(id),
  title text not null,
  description text,
  price int not null,
  condition text check (condition in ('new','used','like_new')) default 'used',
  city text default 'Минск',
  photos text[] default '{}',
  is_active boolean default false,
  status text default 'pending' check (status in ('pending', 'approved', 'rejected')),
  is_vip boolean default false,
  created_at timestamp default now()
);

create table interests (
  id uuid primary key default gen_random_uuid(),
  item_id uuid references items(id) on delete cascade,
  buyer_tg_id bigint not null,
  buyer_username text,
  message text,
  created_at timestamp default now()
);

alter table profiles enable row level security;
alter table items enable row level security;
alter table interests enable row level security;
create policy "Read" on profiles for select using (true);
create policy "Read" on items for select using (true);
create policy "Read" on interests for select using (true);

insert into storage.buckets (id, name, public) values ('item-images', 'item-images', true)
on conflict do nothing;
create policy "Upload" on storage.objects for insert with check (bucket_id = 'item-images');
create policy "ReadImg" on storage.objects for select using (bucket_id = 'item-images');
