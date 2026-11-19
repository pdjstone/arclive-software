"""Markdown: .md titles, and turning descriptions into HTML"""
import hashlib
from pathlib import Path

import toml
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from toml.decoder import TomlDecodeError

from .errors import CatalogueError
from .front_matter import front_matter_plugin

# Inline HTML is allowed, e.g. <kbd>
markdown = MarkdownIt('commonmark').use(front_matter_plugin, marker_chr='+')
QUICKSTART_HEADING = 'quick start'
DOCS_DIR = 'docs'


def text_content(node: SyntaxTreeNode):
    return ''.join(t.content for t in node.to_tokens() if t.type == 'inline')


def render_nodes(nodes):
    tokens = [t for n in nodes for t in n.to_tokens()]
    return markdown.renderer.render(tokens, markdown.options, {})


def heading_level(node: SyntaxTreeNode):
    return int(node.tag[1:])


def parse_markdown_file(md_path):
    """A .md title: returns ({software_id: meta}, {software_id: description
    nodes}), the description being the nodes after the H1"""
    md_path = Path(md_path)
    software_id = md_path.stem
    nodes = SyntaxTreeNode(markdown.parse(md_path.read_text(encoding='utf-8'))).children
    meta = {}
    if nodes and nodes[0].type == 'front_matter':
        try:
            meta = toml.loads(nodes[0].content)
        except TomlDecodeError as e:
            raise CatalogueError(f"TOML error in front matter of {md_path}: {e}")
        nodes = nodes[1:]
    for field in ('title', 'description'):
        if field in meta:
            raise CatalogueError(f"{md_path}: '{field}' comes from the document, "
                                 "so can't be in the front matter")
    if not nodes or nodes[0].tag != 'h1':
        raise CatalogueError(f"{md_path}: must start with front matter and then an H1 (the title)")
    meta['title'] = text_content(nodes[0])
    return {software_id: meta}, {software_id: nodes[1:]}


def is_quickstart(node):
    return node.type == 'heading' and text_content(node).strip().lower() == QUICKSTART_HEADING


def section_nodes(nodes, start, level=None):
    """nodes from start up to the next heading (if level is None), or the
    next one at level or higher (so subheadings are included)"""
    section = []
    for n in nodes[start:]:
        if n.type == 'heading' and (level is None or heading_level(n) <= level):
            break
        section.append(n)
    return section


def convert_description(meta, out_dir, nodes=None):
    """Turns the Markdown description (nodes, or else meta['description'])
    into HTML: the short description, the full one (written to out_dir/docs)
    if it's longer, and the Quick Start section"""
    if nodes is None:
        if 'description' not in meta:
            return
        nodes = SyntaxTreeNode(markdown.parse(meta['description'])).children
    meta.pop('description', None)

    short = section_nodes(nodes, 0)
    if not short:
        # Starts with a heading, so use its section (unless it's the Quick Start)
        for i, n in enumerate(nodes):
            if n.type == 'heading' and not is_quickstart(n):
                short = section_nodes(nodes, i + 1)
                break
    if short:
        meta['description'] = render_nodes(short)
    if len(short) < len(nodes):
        html = render_nodes(nodes)
        hash = hashlib.sha256(html.encode()).hexdigest()
        name = f"{DOCS_DIR}/{meta['id']}-{hash[:12]}.html"
        (Path(out_dir) / name).write_text(html, encoding='utf-8')
        meta['full-description'] = name

    quickstart = None
    for i, n in enumerate(nodes):
        if is_quickstart(n):
            quickstart = section_nodes(nodes, i + 1, heading_level(n))
            break
    if quickstart:
        meta['quickstart'] = render_nodes(quickstart)
