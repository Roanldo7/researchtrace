"""Render the already approved Markdown report to a downloadable PDF in memory."""
import html
import re


def to_html(markdown):
    refs = dict(re.findall(r'^\[([A-Za-z]\d+)\]:\s*(https?://\S+)', markdown, re.M))
    def inline(value):
        parts = re.split(r'(https?://[^\s（）]+|\[[A-Za-z]\d+\])', value)
        output = []
        for part in parts:
            if part.startswith(('https://', 'http://')):
                safe = html.escape(part, quote=True)
                output.append(f'<a href="{safe}">{safe}</a>')
            elif re.fullmatch(r'\[[A-Za-z]\d+\]', part) and part[1:-1] in refs:
                sid = part[1:-1]
                output.append(f'<a href="{html.escape(refs[sid], quote=True)}">[{sid}]</a>')
            else:
                output.append(html.escape(part))
        return ''.join(output)
    blocks, paragraph, rows, bullets = [], [], [], []
    def flush():
        if paragraph:
            blocks.append('<p>' + inline(' '.join(paragraph)) + '</p>'); paragraph.clear()
        if rows:
            cells = [[x.strip() for x in line.strip().strip('|').split('|')] for line in rows]
            cells = [r for r in cells if not all(re.fullmatch(r':?-{2,}:?', x) for x in r)]
            if cells:
                blocks.append('<table>' + ''.join('<tr>' + ''.join(
                    f'<{("th" if i == 0 else "td")}>{inline(cell)}</{("th" if i == 0 else "td")}>'
                    for cell in row) + '</tr>' for i, row in enumerate(cells)) + '</table>')
            rows.clear()
        if bullets:
            blocks.append('<ul>' + ''.join('<li>' + inline(x) + '</li>' for x in bullets) + '</ul>')
            bullets.clear()
    for line in markdown.splitlines():
        if re.match(r'^\[[A-Za-z]\d+\]:\s*https?://', line):
            flush(); continue
        if not line.strip():
            flush(); continue
        if line.startswith('#'):
            flush(); level = min(len(line) - len(line.lstrip('#')), 3)
            blocks.append(f'<h{level}>' + inline(line[level:].strip()) + f'</h{level}>')
        elif line.startswith('|'):
            if paragraph or bullets: flush()
            rows.append(line)
        elif line.startswith('- '):
            if paragraph or rows: flush()
            bullets.append(line[2:])
        else:
            if rows or bullets: flush()
            paragraph.append(line.strip())
    flush()
    if refs:
        blocks.append('<h2>来源链接</h2><ul>' + ''.join(
            f'<li><a href="{html.escape(url, quote=True)}">[{sid}] {html.escape(url)}</a></li>'
            for sid, url in refs.items()) + '</ul>')
    return '<html><body>' + ''.join(blocks) + '</body></html>'


def render(markdown):
    try:
        import pymupdf
    except ImportError as exc:
        raise ValueError('此电脑尚未安装 PDF 导出组件 PyMuPDF；Markdown 下载仍可使用') from exc
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError('报告内容为空，无法导出 PDF')
    css = '''body{font-family:sans-serif;color:#17324d;font-size:10.5pt;line-height:1.55}
        h1{font-size:20pt;color:#17324d;margin:0 0 16pt}h2{font-size:14pt;color:#1769a8;margin:17pt 0 7pt}
        h3{font-size:11.5pt;margin:12pt 0 5pt}p{margin:0 0 9pt}a{color:#135a9c}
        table{border-collapse:collapse;width:100%;margin:8pt 0 12pt;font-size:9pt}
        th,td{border:0.5pt solid #cbd8e5;padding:5pt}th{background:#eaf2fb}
        li{margin:0 0 5pt;overflow-wrap:anywhere}'''
    story = pymupdf.Story(to_html(markdown), user_css=css)
    page = pymupdf.paper_rect('a4')
    where = page + (43, 47, -43, -47)
    def rectangle(number, _filled):
        if number > 40:
            raise ValueError('报告超过40页，PDF导出已停止')
        return page, where, None
    document = story.write_with_links(rectangle)
    return document.tobytes()
