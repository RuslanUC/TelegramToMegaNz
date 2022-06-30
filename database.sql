CREATE TABLE public.accounts (
    "_id" int4 NOT NULL GENERATED ALWAYS AS IDENTITY,
    login varchar NOT NULL,
    "password" varchar NOT NULL,
    user_hash varchar NOT NULL DEFAULT '',
    password_aes varchar NOT NULL DEFAULT '',
    free_space int8 NOT NULL DEFAULT '20000000000'::bigint
);