import sys
from typing import List
from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from markdown_it.token import Token
from better_front_matter_plugin import better_front_matter_plugin


def text_content(node: SyntaxTreeNode):
    content = ''
    for t in node.to_tokens():
        if t.type == 'inline':
            content += t.content
    return content

def heading_level(tag: str) -> int:
    assert tag[0] == 'h'
    level = tag[1]
    assert '0' <= level <= '9'
    return int(level)

def find_tag(tokens: List[Token], tag: str):
    root = SyntaxTreeNode(tokens)
    for n in root.children:
        if n.tag == tag:
            return n

def extract_subsection(tokens: List[Token], section_heading: str):
    root = SyntaxTreeNode(tokens)

    section_tokens = []
    section_heading_level = 0
    started = False

    for n in root.children:
        if n.type == 'heading' and text_content(n) == section_heading:
            started = True
            section_heading_level = heading_level(n.tag)
        elif started and n.type == 'heading':
            if heading_level(n.tag) <= section_heading_level:
                break
        if started:
            section_tokens.extend(n.to_tokens())
    
    return section_tokens

if __name__ == '__main__':
    md_path = sys.argv[1]
    with open(md_path, 'r') as f:
        md_src = f.read()

    md = MarkdownIt().use(better_front_matter_plugin, marker_chr='+')

    tokens = md.parse(md_src)
    fm_token = next((t for t in tokens if t.type=='front_matter'), None)

    print(fm_token.content)

    quickstart_tokens = extract_subsection(tokens, 'Quick Start')
    
    print(md.renderer.render(quickstart_tokens, {}, {}))

    main_heading = find_tag(tokens, 'h1')
    print(text_content(main_heading))