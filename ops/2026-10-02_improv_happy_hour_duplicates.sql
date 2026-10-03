-- Confirmed pre-publisher-scoping Tribe ID collision: all three publishers used
-- numeric ID 10011650. Only 425e0db3... matches the current Seattle source event.
-- Applied with equivalent guarded REST PATCHes at 2026-10-03 00:54:14 UTC.
-- Hide, never delete; normal source sync does not write hidden_at.
begin;

do $$
begin
  if not exists (
    select 1 from public.events
    where id = 'f92766bd-4de9-4382-a53c-aeb4f6cf27da'
      and external_source = 'mapsee'
      and external_id = '425e0db3adb20d1027d4cdf7e5e23a47dd19a7c3'
      and title = 'Improv Happy Hour'
      and starts_at = '2026-10-03T01:00:00Z'
      and hidden_at is null
      and description like '%Tickets / info: https://www.eventbrite.com/e/1977544308260?aff=oddtdtcreator%'
  ) then
    raise exception 'The verified Seattle listing is missing; refusing repair';
  end if;
end $$;

update public.events e
set hidden_at = '2026-10-03T00:54:14.280561Z'
where e.external_source = 'mapsee'
  and e.claimed_at is null and e.is_private = false and e.hidden_at is null
  and e.title = 'Improv Happy Hour'
  and e.place_name = 'Unexpected Productions'
  and e.starts_at = '2026-10-03T01:00:00Z'
  and (
    (e.id = '1448b567-1ba9-4422-9a28-9195e9f66432'
      and e.external_id = 'cc4ed5c2bfb67b5babc4f9049bfc2752401afc73'
      and e.updated_at = '2026-09-21T14:27:01.753751Z'
      and e.description like '%Tickets / info: https://blessingtonparish.ie/event/st-brigids-manor-kilbride-2-2/2026-09-21/%')
    or
    (e.id = '91e624ce-043a-46b8-b317-db630e5c12a7'
      and e.external_id = 'c56668ff4c052b0fb7702c5f75896335b9e0711a'
      and e.updated_at = '2026-09-28T16:38:35.52844Z'
      and e.description like '%Tickets / info: https://www.lockleazent.co.uk/event/20003/2026-11-02/%')
  )
returning e.id, e.hidden_at;

select id, title, hidden_at from public.events
where id in ('1448b567-1ba9-4422-9a28-9195e9f66432',
             '91e624ce-043a-46b8-b317-db630e5c12a7',
             'f92766bd-4de9-4382-a53c-aeb4f6cf27da');
commit;

-- Reversal, only if deliberately needed; matches this repair's exact timestamp.
-- update public.events set hidden_at = null
-- where id in ('1448b567-1ba9-4422-9a28-9195e9f66432',
--              '91e624ce-043a-46b8-b317-db630e5c12a7')
--   and external_source = 'mapsee' and claimed_at is null and is_private = false
--   and hidden_at = '2026-10-03T00:54:14.280561Z';
