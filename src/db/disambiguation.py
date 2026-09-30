"""Admin review of exact sets, preserving suppressed assignments for audit."""

from urllib.parse import urlsplit

from src.config.constants import CONTENT_RECOVERY_MAX_GENERATION, REPORT_THRESHOLD
from src.db import POOL
from src.services.media import IMAGE_EXTENSIONS, PROXIED_MEDIA_HOSTS, VIDEO_EXTENSIONS


def direct_preview(url: str):
    """Describe direct assets without making metadata requests."""
    parsed = urlsplit(url)
    extension = '.' + parsed.path.rsplit('.', 1)[-1].lower()
    if parsed.scheme == 'https' and parsed.hostname in PROXIED_MEDIA_HOSTS:
        if extension in IMAGE_EXTENSIONS:
            return dict(kind='image', url=url)
        if extension in VIDEO_EXTENSIONS:
            return dict(kind='video', url=url)
    return None


def list_review_sets():
    with POOL.connection() as conn, conn.cursor() as cur:
        cur.execute("""
            WITH images AS (
                SELECT set_key, url, MIN(content_link_id) AS anchor_id,
                       MAX(uploaded_date) AS posted,
                       COUNT(DISTINCT role_id) AS roles,
                       SUM(num_reports) FILTER (WHERE NOT disambiguated) AS reports,
                       BOOL_AND(disambiguated) AS reviewed
                FROM content_links
                WHERE set_key IS NOT NULL AND NOT is_dead
                GROUP BY set_key, url
            )
            SELECT set_key, MIN(anchor_id), COUNT(*),
                   COUNT(*) FILTER (WHERE NOT reviewed AND roles > 1),
                   COALESCE(SUM(reports), 0), MAX(posted)
            FROM images
            GROUP BY set_key
            HAVING COUNT(*) FILTER (WHERE NOT reviewed AND roles > 1) > 0
            ORDER BY COALESCE(SUM(reports), 0) DESC, COUNT(*) DESC,
                     MAX(posted) DESC NULLS LAST, set_key
            LIMIT 100
        """)
        return [dict(key=r[0], anchor_id=r[1], size=r[2], remaining=r[3],
                     reports=r[4], date=r[5].isoformat() if r[5] else None)
                for r in cur.fetchall()]


def get_review_set(anchor_id: int):
    with POOL.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT set_key FROM content_links WHERE content_link_id = %s", (anchor_id,))
        anchor = cur.fetchone()
        if not anchor or not anchor[0]:
            return None
        cur.execute("""
            SELECT cl.content_link_id, cl.url, cl.role_id,
                   COALESCE(ri.member_name, cl.role_id), ri.group_name,
                   cl.disambiguated, cl.num_reports
            FROM content_links cl LEFT JOIN role_info ri USING (role_id)
            WHERE NOT cl.is_dead AND cl.url IN (
                SELECT url FROM content_links WHERE set_key = %s AND NOT is_dead
            )
            ORDER BY cl.content_link_id
        """, (anchor[0],))
        items = {}
        for lid, url, rid, member, group, reviewed, reports in cur.fetchall():
            item = items.setdefault(url, dict(id=lid, url=url, roles={}, reviewed=True,
                                             preview=direct_preview(url)))
            item['reviewed'] = item['reviewed'] and reviewed
            item['roles'][rid] = dict(id=rid, name=member, label=f"{member} ({group})" if group else member,
                                     selected=reviewed and reports < REPORT_THRESHOLD)
        return dict(key=anchor[0], items=[{**item, 'roles': list(item['roles'].values())}
                                        for item in items.values()])


def save_review(content_link_id: int, selected: list[str], expected: list[str]):
    """Validate the full candidate list under locks; update all copies of the URL."""
    with POOL.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT url FROM content_links WHERE content_link_id = %s", (content_link_id,))
        row = cur.fetchone()
        if not row:
            raise LookupError('Image no longer exists')
        cur.execute("SELECT content_link_id, role_id FROM content_links WHERE url = %s ORDER BY content_link_id FOR UPDATE", (row[0],))
        rows = cur.fetchall()
        roles = {r[1] for r in rows}
        if roles != set(expected):
            raise LookupError('Labels changed. Reload this set before saving.')
        if not set(selected) <= roles:
            raise ValueError('Select only labels offered for this image')
        cur.execute("""
            UPDATE content_links SET disambiguated = TRUE,
                num_reports = CASE WHEN role_id = ANY(%s::text[]) THEN 0
                                   ELSE GREATEST(num_reports, %s) END
            WHERE url = %s
        """, (selected, REPORT_THRESHOLD, row[0]))
        return cur.rowcount


def mark_broken(content_link_id: int):
    """Mark every assignment of this URL dead; leave recovery available."""
    with POOL.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT url FROM content_links WHERE content_link_id = %s", (content_link_id,))
        row = cur.fetchone()
        if not row:
            raise LookupError('Image no longer exists')
        cur.execute("""
            UPDATE content_links SET is_dead = TRUE,
                last_checked_at = NOW(), last_check_status = 'dead',
                last_check_error = 'Marked broken by admin review',
                is_recovery_exhausted = (COALESCE(recovery_generation, 0) >= %s)
            WHERE url = %s
        """, (CONTENT_RECOVERY_MAX_GENERATION, row[0]))
        return cur.rowcount
