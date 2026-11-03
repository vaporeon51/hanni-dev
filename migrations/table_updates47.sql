-- Active ping roles published in Discord channel 779827588180869152,
-- messages 1566051666565009480 through 1566051722344931439 (2026-10-31).
-- Update metadata only: preserve birthdays, portraits, Elo, counts and old roles.
BEGIN;
WITH roles (role_id, string_tag, member_name, group_name) AS (
    VALUES
    ('1074792475912327169', 'MADEIN', '', 'MADEIN'),
    ('1358988947413532917', 'SAY MY NAME', '', 'SAY MY NAME'),
    ('1378547553590710353', 'ifeye', '', 'ifeye'),
    ('1394885483271553206', 'Baby DONT Cry', '', 'Baby DONT Cry'),
    ('1481192603193053226', 'Real Ones', '', 'Real Ones'),
    ('1495588316521693257', 'UNCHILD', '', 'UNCHILD'),
    ('1495588816684322907', 'AtHeart', '', 'AtHeart'),
    ('1502220988123844700', 'KIIRAS', '', 'KIIRAS'),
    ('1540511962646192229', 'OURBIRTHDAY', '', 'OURBIRTHDAY'),
    ('1552799767959183360', 'HITGS', '', 'HITGS'),
    ('1552799937572503672', 'FIFTY FIFTY', '', 'FIFTY FIFTY'),
    ('1560336796385021982', 'A2O LITE', '', 'A2O LITE'),
    ('1566036766547378238', 'Athena [FIFTY FIFTY]', 'Athena', 'FIFTY FIFTY'),
    ('779822389647114271', 'J [STAYC]', 'J', 'STAYC'),
    ('779822392359911425', 'Sumin [STAYC]', 'Sumin', 'STAYC'),
    ('779822395547844659', 'Sieun [STAYC]', 'Sieun', 'STAYC'),
    ('779822396831301643', 'Isa [STAYC]', 'Isa', 'STAYC'),
    ('779822398719000607', 'Seeun [STAYC]', 'Seeun', 'STAYC'),
    ('779822399758532608', 'Yoon [STAYC]', 'Yoon', 'STAYC'),
    ('779848040235794443', 'Nagyung [fromis_9]', 'Nagyung', 'fromis_9'),
    ('779849462499508254', 'Gahyun [Dreamcatcher]', 'Gahyun', 'Dreamcatcher'),
    ('779856224618938438', 'Miyeon [i-dle]', 'Miyeon', 'i-dle'),
    ('779856689943281694', 'Minnie [i-dle]', 'Minnie', 'i-dle'),
    ('779857001697771520', 'Soyeon [i-dle]', 'Soyeon', 'i-dle'),
    ('779857131893030932', 'Yuqi [i-dle]', 'Yuqi', 'i-dle'),
    ('779857250381987860', 'Shuhua [i-dle]', 'Shuhua', 'i-dle'),
    ('785670300171894844', 'Nana [WOOAH]', 'Nana', 'WOOAH'),
    ('798063140298293298', 'Wooyeon [WOOAH]', 'Wooyeon', 'WOOAH'),
    ('906301154407907358', 'Yeseo [MADEIN]', 'Yeseo', 'MADEIN'),
    ('906302029301944321', 'Mashiro [MADEIN]', 'Mashiro', 'MADEIN'),
    ('945907369043447829', 'Bae [NMIXX]', 'Bae', 'NMIXX')
)
INSERT INTO role_info (role_id, string_tag, member_name, group_name, member_group_array)
SELECT role_id, string_tag, member_name, group_name,
       array_cat(
           string_to_array(regexp_replace(lower(trim(member_name)), '[^a-z0-9\s]', '', 'g'), ' '),
           string_to_array(regexp_replace(lower(trim(group_name)), '[^a-z0-9\s]', '', 'g'), ' ')
       )
FROM roles
ON CONFLICT (role_id) DO UPDATE SET
    string_tag = EXCLUDED.string_tag,
    member_name = EXCLUDED.member_name,
    group_name = EXCLUDED.group_name,
    member_group_array = EXCLUDED.member_group_array;

-- Migration 13 copied the numeric ID into string_tag. Only remove a suffix
-- matching this row's own ID; this also repairs historical/inactive roles.
UPDATE role_info
SET string_tag = left(string_tag, length(string_tag) - length(role_id) - 1)
WHERE right(string_tag, length(role_id) + 1) = ' ' || role_id;
COMMIT;
