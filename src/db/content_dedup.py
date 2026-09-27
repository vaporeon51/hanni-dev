"""Transactional Discord-first cleanup, with a durable archive and tombstones."""
from datetime import timedelta
from psycopg.types.json import Jsonb
from src.content_dedup import (
    MediaSet, MediaSetIndex, candidate_posts, exact_match, VISUAL_REVIEW_REASON,
)

LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('hanni:cross-source-dedup'))"


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def load_sets(cursor, roots=None):
    query = '''SELECT role_id, url, uploaded_date, goyangi_content_id,
                            original_url, source_kind, root_message_id,
                            source_message_id, goyangi_set_id
                     FROM content_links
                     WHERE source_kind = 'goyangi' OR source_message_id IS NOT NULL'''
    params = ()
    if roots is not None:
        query = query.replace(
            "WHERE source_kind = 'goyangi' OR source_message_id IS NOT NULL",
            "WHERE source_kind = 'goyangi' OR (source_message_id IS NOT NULL "
            "AND COALESCE(root_message_id, source_message_id) = ANY(%s))",
        )
        params = (list(roots),)
    cursor.execute(query, params)
    discord, goyangi = {}, {}
    for role, url, created, cid, original, kind, root, message, sid in cursor.fetchall():
        role, url, cid, original, kind, root, message, sid = map(
            _text, (role, url, cid, original, kind, root, message, sid)
        )
        if kind == 'goyangi':
            if not sid:
                continue
            # Stored links can be a subset of the upstream set, so they are
            # useful for shortlisting but never enough to prove set equality.
            goyangi.setdefault(sid, MediaSet(sid, complete=False)).add(
                role, url, created, cid, original
            )
            continue
        elif message:
            target, key = discord, root or message
        else:
            continue
        target.setdefault(key, MediaSet(key)).add(role, url, created, cid, original)
    return list(discord.values()), list(goyangi.values())


def blocked_sets(cursor):
    cursor.execute('SELECT set_id FROM goyangi_duplicate_sets')
    return {row[0] for row in cursor.fetchall()}


def blocked_contents(cursor):
    cursor.execute('SELECT content_id, role_id FROM goyangi_duplicate_contents')
    return {(str(content_id), str(role_id)) for content_id, role_id in cursor.fetchall()}


def remove_set(cursor, set_id, root_id, evidence):
    """Caller holds shared xact lock. Never delete or update a Discord row."""
    cursor.execute('''SELECT 1 FROM content_links
                     WHERE source_kind IS DISTINCT FROM 'goyangi'
                       AND source_message_id IS NOT NULL
                       AND COALESCE(root_message_id, source_message_id) = %s LIMIT 1''', (root_id,))
    if cursor.fetchone() is None:
        raise ValueError(f'Discord post {root_id} no longer exists')
    cursor.execute('SELECT discord_root_id FROM goyangi_duplicate_sets WHERE set_id=%s', (set_id,))
    prior = cursor.fetchone()
    if prior and prior[0] != root_id:
        raise ValueError(f'Goyangi set {set_id} is already suppressed for another Discord post')
    cursor.execute('''INSERT INTO goyangi_duplicate_sets(set_id, discord_root_id, evidence)
                      VALUES (%s,%s,%s) ON CONFLICT (set_id) DO NOTHING''',
                   (set_id, root_id, Jsonb(evidence)))
    cursor.execute('''INSERT INTO goyangi_duplicate_archive(content_link_id, set_id, row_data)
                      SELECT content_link_id, goyangi_set_id, to_jsonb(cl)
                      FROM content_links cl
                      WHERE source_kind = 'goyangi' AND source_message_id IS NULL
                        AND goyangi_set_id = %s
                      ON CONFLICT (content_link_id) DO NOTHING''', (set_id,))
    cursor.execute('''DELETE FROM content_links
                      WHERE source_kind = 'goyangi' AND source_message_id IS NULL
                        AND goyangi_set_id = %s''', (set_id,))
    deleted = cursor.rowcount
    cursor.execute('DELETE FROM goyangi_pending_sets WHERE set_id = %s', (set_id,))
    return deleted


def remove_content(cursor, content_id, role_id, set_id, root_id, evidence):
    """Archive and suppress one confirmed duplicate clip, keeping its set intact."""
    cursor.execute('''SELECT 1 FROM content_links
                     WHERE source_kind IS DISTINCT FROM 'goyangi'
                       AND source_message_id IS NOT NULL
                       AND role_id = %s
                       AND COALESCE(root_message_id, source_message_id) = %s LIMIT 1''',
                   (role_id, root_id))
    if cursor.fetchone() is None:
        raise ValueError(f'Discord post {root_id} no longer exists for role {role_id}')
    cursor.execute('''SELECT discord_root_id FROM goyangi_duplicate_contents
                     WHERE content_id = %s AND role_id = %s''', (content_id, role_id))
    prior = cursor.fetchone()
    if prior and prior[0] != root_id:
        raise ValueError(f'Goyangi content {content_id} is already suppressed for another Discord post')
    cursor.execute('''SELECT goyangi_set_id FROM content_links
                     WHERE source_kind = 'goyangi'
                       AND goyangi_content_id = %s AND role_id = %s LIMIT 1''',
                   (content_id, role_id))
    row = cursor.fetchone()
    if row is not None and row[0] != set_id:
        raise ValueError(f'Goyangi content {content_id} is no longer in set {set_id}')
    cursor.execute('''INSERT INTO goyangi_duplicate_contents(content_id, role_id, discord_root_id, evidence)
                      VALUES (%s,%s,%s,%s) ON CONFLICT (content_id, role_id) DO NOTHING''',
                   (content_id, role_id, root_id, Jsonb(evidence)))
    cursor.execute('''INSERT INTO goyangi_duplicate_archive(content_link_id, set_id, row_data)
                      SELECT content_link_id, goyangi_set_id, to_jsonb(cl)
                      FROM content_links cl
                      WHERE source_kind = 'goyangi'
                        AND goyangi_content_id = %s AND role_id = %s
                      ON CONFLICT (content_link_id) DO NOTHING''', (content_id, role_id))
    cursor.execute('''DELETE FROM content_links
                      WHERE source_kind = 'goyangi'
                        AND goyangi_content_id = %s AND role_id = %s''', (content_id, role_id))
    return cursor.rowcount


def reconcile_exact(cursor, roots=None):
    """Recheck exact matches and queue likely cross-host matches after Discord writes."""
    if roots is not None and not roots:
        return 0
    discord, goyangi = load_sets(cursor, roots=roots)
    index = MediaSetIndex(discord)
    removed = 0
    for item in goyangi:
        match = exact_match(item, index)
        if match:
            post, evidence = match
            removed += remove_set(cursor, item.id, post.id, evidence)
        elif candidate_posts(item, index):
            # The stored rows may be only a subset. Let the Goyangi worker
            # hydrate the source set before making a set-wide decision.
            cursor.execute('''INSERT INTO goyangi_pending_sets(set_id,payload,reason,retry_after)
                              VALUES (%s,%s,%s,NOW()) ON CONFLICT(set_id) DO UPDATE
                              SET reason=EXCLUDED.reason,retry_after=NOW()''',
                           (item.id, Jsonb({'id': item.id, 'existing_only': True}),
                            'Discord post candidate'))
    return removed


def defer_set(cursor, item, reason):
    delay = timedelta(days=1) if reason == VISUAL_REVIEW_REASON else timedelta(hours=1)
    cursor.execute('''INSERT INTO goyangi_pending_sets(set_id,payload,reason,retry_after)
                      VALUES (%s,%s,%s,NOW() + %s)
                      ON CONFLICT(set_id) DO UPDATE SET payload=EXCLUDED.payload,
                        reason=EXCLUDED.reason, retry_after=EXCLUDED.retry_after''',
                   (item['id'], Jsonb(item), reason, delay))
