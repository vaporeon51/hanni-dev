-- Group-role age filtering must use the youngest member's birthday.
-- A2O LITE: Aijing, born 2013-04-24.
-- Sources:
-- https://fansupport.joelin.cc/en/group/a2o-lite/
-- https://www.kpopfromseoul.com/2026/10/a2o-lite-members-birthdays-ages-profiles.html
-- Replace the legacy 1900 placeholder; preserve all other role metadata.
BEGIN;
UPDATE role_info
SET birthday = TIMESTAMP '2013-04-24 00:00:00'
WHERE role_id = '1560336796385021982';
COMMIT;
