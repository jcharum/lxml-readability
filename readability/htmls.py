from cleaners import normalize_spaces, clean_attributes, html_cleaner
from encoding import get_encoding
from lxml.html import tostring
import logging
import lxml.html
import re
import unittest

logging.getLogger().setLevel(logging.DEBUG)

utf8_parser = lxml.html.HTMLParser(encoding='utf-8')

def build_doc(page):
    enc = get_encoding(page)
    page_enc = page.decode(enc, 'replace').encode('utf-8')
    doc = lxml.html.document_fromstring(page_enc, parser=utf8_parser)
    return doc

def js_re(src, pattern, flags, repl):
    return re.compile(pattern, flags).sub(src, repl.replace('$', '\\'))


def normalize_entities(cur_title):
    entities = {
        u'\u2014':'-',
        u'\u2013':'-',
        u'&mdash;': '-',
        u'&ndash;': '-',
        u'\u00A0': ' ',
        u'\u00AB': '"',
        u'\u00BB': '"',
        u'&quot;': '"',
    }
    for c, r in entities.iteritems():
        if c in cur_title:
            cur_title = cur_title.replace(c, r)

    return cur_title

def norm_title(title):
    return normalize_entities(normalize_spaces(title))

def get_title(doc):
    titleElem = doc.find('.//title')
    if titleElem is None:
        return ''

    title = titleElem.text
    if title is None:
        return ''
    
    return norm_title(title)

def add_match(collection, text, orig):
    text = norm_title(text)
    if len(text.split()) >= 2 and len(text) >= 15:
        if text.replace('"', '') in orig.replace('"', ''):
            collection.add(text)

def shorten_title(doc):
    title = orig = get_title(doc)
    if title == '':
        return ''

    candidates = set()

    for item in ['.//h1', './/h2', './/h3']:
        for e in list(doc.iterfind(item)):
            if e.text:
                add_match(candidates, e.text, orig)
            if e.text_content():
                add_match(candidates, e.text_content(), orig)

    for item in ['#title', '#head', '#heading', '.pageTitle', '.news_title', '.title', '.head', '.heading', '.contentheading', '.small_header_red']:
        for e in doc.cssselect(item):
            if e.text:
                add_match(candidates, e.text, orig)
            if e.text_content():
                add_match(candidates, e.text_content(), orig)
                
    if candidates:
        title = sorted(candidates, key=len)[-1]
    else:
        for delimiter in [' | ', ' - ', ' :: ', ' / ']:
            if delimiter in title:
                parts = orig.split(delimiter)
                if len(parts[0].split()) >= 4:
                    title = parts[0]
                    break
                elif len(parts[-1].split()) >= 4:
                    title = parts[-1]
                    break
        else:
            if ': ' in title:
                parts = orig.split(': ')
                if len(parts[-1].split()) >= 4:
                    title = parts[-1]
                else:
                    title = orig.split(': ', 1)[1]

    if not 15 < len(title) < 150:
        return orig

    return title

def get_body(doc):
    [ elem.drop_tree() for elem in doc.xpath('.//script | .//link | .//style') ]
    raw_html = unicode(tostring(doc.body or doc))
    cleaned = clean_attributes(raw_html)
    try:
        #BeautifulSoup(cleaned) #FIXME do we really need to try loading it?
        return cleaned
    except Exception: #FIXME find the equivalent lxml error
        logging.error("cleansing broke html content: %s\n---------\n%s" % (raw_html, cleaned))
        return raw_html

def tags(node, *tag_names):
    for tag_name in tag_names:
        for e in node.findall('.//%s' % tag_name):
            yield e

def clean(text):
    text = re.sub('\s*\n\s*', '\n', text)
    text = re.sub('[ \t]{2,}', ' ', text)
    return text.strip()

def parse(input, url):
    logging.debug('parse url: %s', url)
    raw_doc = build_doc(input)
    doc = html_cleaner.clean_html(raw_doc)
    if url:
        doc.make_links_absolute(url, resolve_base_href=True)
    else:
        doc.resolve_base_href()
    return doc
