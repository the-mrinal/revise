-- Fill users from Supabase's auth.users, where that schema exists.
--
-- On the Supabase database this copies every account (same UUIDs) into the
-- new users table; the server then adds anyone who signs up later the first
-- time they use the API (database.ensure_user). On our own Postgres there is
-- no auth schema, so this does nothing; users arrive with the data copy.
DO $$
BEGIN
  IF to_regclass('auth.users') IS NOT NULL THEN
    EXECUTE $sql$
      INSERT INTO public.users (id, email, created_at, last_sign_in_at, source)
      SELECT id, email, created_at, last_sign_in_at, 'supabase'
      FROM auth.users
      ON CONFLICT (id) DO UPDATE
        SET email = EXCLUDED.email,
            last_sign_in_at = GREATEST(public.users.last_sign_in_at, EXCLUDED.last_sign_in_at)
    $sql$;
  END IF;
END
$$;
