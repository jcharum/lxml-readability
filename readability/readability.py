#!/usr/bin/env python
from cleaners import html_cleaner, clean_attributes
from collections import defaultdict
from htmls import build_doc, get_body, get_title, shorten_title
from lxml.etree import tostring, tounicode
from lxml.html import fragment_fromstring, document_fromstring
from lxml.html import builder as B
import logging
import re
import sys
import unittest
import urlparse

logging.basicConfig(level=logging.INFO)

REGEXES = {
    'unlikelyCandidatesRe': re.compile('combx|comment|community|disqus|extra|foot|header|menu|remark|rss|shoutbox|sidebar|sponsor|ad-break|agegate|pagination|pager|popup|tweet|twitter',re.I),
    'okMaybeItsACandidateRe': re.compile('and|article|body|column|main|shadow',re.I),
    'positiveRe': re.compile('article|body|content|entry|hentry|main|page|pagination|post|text|blog|story',re.I),
    'negativeRe': re.compile('combx|comment|com-|contact|foot|footer|footnote|masthead|media|meta|outbrain|promo|related|scroll|shoutbox|sidebar|sponsor|shopping|tags|tool|widget',re.I),
    'divToPElementsRe': re.compile('<(a|blockquote|dl|div|img|ol|p|pre|table|ul)',re.I),
    #'replaceBrsRe': re.compile('(<br[^>]*>[ \n\r\t]*){2,}',re.I),
    #'replaceFontsRe': re.compile('<(\/?)font[^>]*>',re.I),
    #'trimRe': re.compile('^\s+|\s+$/'),
    #'normalizeRe': re.compile('\s{2,}/'),
    #'killBreaksRe': re.compile('(<br\s*\/?>(\s|&nbsp;?)*){1,}/'),
    #'videoRe': re.compile('http:\/\/(www\.)?(youtube|vimeo)\.com', re.I),
    #skipFootnoteLink:      /^\s*(\[?[a-z0-9]{1,2}\]?|^|edit|citation needed)\s*$/i,
}

def describe(node, depth=1):
    if not hasattr(node, 'tag'):
        return "[%s]" % type(node)
    name = node.tag
    if node.get('id', ''): name += '#'+node.get('id') 
    if node.get('class', ''): 
        name += '.' + node.get('class').replace(' ','.')
    if name[:4] in ['div#', 'div.']:
        name = name[3:]
    if depth and node.getparent() is not None:
        return name+' - '+describe(node.getparent(), depth-1)
    return name

def to_int(x):
    if not x: return None
    x = x.strip()
    if x.endswith('px'):
        return int(x[:-2]) 
    if x.endswith('em'):
        return int(x[:-2]) * 12 
    return int(x)

def clean(text):
    text = re.sub('\s*\n\s*', '\n', text)
    text = re.sub('[ \t]{2,}', ' ', text)
    return text.strip()

def text_length(i):
    return len(clean(i.text_content() or ""))

def clean_segment_extension(num_segments, index, segment):
    if segment.find('.') == -1:
        return segment
    else:
        split_segment = segment.split('.')
        possible_type = split_segment[1]
        has_non_alpha = re.search(r'[^a-zA-Z]', possible_type)
        if has_non_alpha:
            return segment
        else:
            return split_segment[0]

def clean_segment_ewcms(num_segments, index, segment):
    """
    EW-CMS specific segment cleaning.  Quoth the original source:
        "EW-CMS specific segment replacement. Ugly.
         Example: http://www.ew.com/ew/article/0,,20313460_20369436,00.html"
    """
    return segment.replace(',00', '')

def clean_segment_page_number(num_segments, index, segment):
    # If our first or second segment has anything looking like a page number,
    # remove it.
    if index >= (num_segments - 2):
        pattern = r'((_|-)?p[a-z]*|(_|-))[0-9]{1,2}$'
        cleaned = re.sub(pattern, '', segment, re.IGNORECASE)
        if cleaned == '':
            return None
        else:
            return cleaned
    else:
        return segment

def clean_segment_number(num_segments, index, segment):
    # If this is purely a number, and it's the first or second segment, it's
    # probably a page number.  Remove it.
    if index >= (num_segments - 2) and re.search(r'^\d{1,2}$', segment):
        return None
    else:
        return segment

def clean_segment_index(num_segments, index, segment):
    if index == (num_segments - 1) and segment.lower() == 'index':
        return None
    else:
        return segment


def clean_segment(num_segments, index, segment):
    """
    Cleans a single segment of a URL to find the base URL.  The base URL is as
    a reference when evaluating URLs that might be next-page links.  Returns a
    cleaned segment string or None, if the segment should be omitted entirely
    from the base URL.
    """
    funcs = [
            clean_segment_extension,
            clean_segment_ewcms,
            clean_segment_page_number,
            clean_segment_number,
            clean_segment_index
            ]
    cleaned_segment = segment
    for func in funcs:
        if cleaned_segment is None:
            break
        cleaned_segment = func(num_segments, index, cleaned_segment)
    return cleaned_segment

def filter_none(seq):
    return [x for x in seq if x is not None]

def clean_segments(segments):
    cleaned = [
            clean_segment(len(segments), i, s)
            for i, s in enumerate(segments)
            ]
    return filter_none(cleaned)

def find_base_url(url):
    if url is None:
        return None
    parts = urlparse.urlsplit(url)
    segments = parts.path.split('/')
    cleaned_segments = clean_segments(segments)
    new_path = '/'.join(cleaned_segments)
    new_parts = (parts.scheme, parts.netloc, new_path, '', '')
    return urlparse.urlunsplit(new_parts)

class Unparseable(ValueError):
    pass

class Summary:
    '''
    The type of object returned by Document.summary().  This includes the
    confidence level we have in our summary.  If this is low (<35), our summary
    may not be valid, though we did our best.
    '''

    def __init__(self, confidence, html):
        self.confidence = confidence
        self.html = html

class Document:
    TEXT_LENGTH_THRESHOLD = 25
    RETRY_LENGTH = 250

    def __init__(self, input, **options):
        self.input = input
        self.options = defaultdict(lambda: None)
        for k, v in options.items():
            self.options[k] = v
        if not self.options['urlfetch']:
            self.options['urlfetch'] = urlfetch.UrlFetch()
        self.html = None

    def _html(self, force=False):
        if force or self.html is None:
            self.html = self._parse(self.input)
        return self.html
    
    def _parse(self, input):
        doc = build_doc(input)
        doc = html_cleaner.clean_html(doc)
        base_href = self.options['url']
        if base_href:
            doc.make_links_absolute(base_href, resolve_base_href=True)
        else:
            doc.resolve_base_href()
        return doc

    def content(self):
        return get_body(self._html(True))
    
    def title(self):
        return get_title(self._html(True))

    def short_title(self):
        return shorten_title(self._html(True))

    def summary(self):
        try:
            ruthless = True
            while True:
                self._html(True)
                
                for i in self.tags(self.html, 'script', 'style'):
                    i.drop_tree()
                for i in self.tags(self.html, 'body'):
                    i.set('id', 'readabilityBody')
                if ruthless: 
                    self.remove_unlikely_candidates()
                self.transform_misused_divs_into_paragraphs()
                candidates = self.score_paragraphs()
                
                best_candidate = self.select_best_candidate(candidates)
                if best_candidate:
                    confidence = best_candidate['content_score']
                    article = self.get_article(candidates, best_candidate)
                else:
                    if ruthless:
                        logging.debug("ruthless removal did not work. ")
                        ruthless = False
                        self.debug("ended up stripping too much - going for a safer _parse")
                        # try again
                        continue
                    else:
                        logging.debug("Ruthless and lenient parsing did not work. Returning raw html")
                        confidence = 0;
                        article = self.html.find('body') or self.html

                unicode_cleaned_article = self.sanitize(article, candidates)
                cleaned_doc = fragment_fromstring(unicode_cleaned_article)
                cleaned_article = tostring(cleaned_doc)

                of_acceptable_length = len(cleaned_article or '') >= (self.options['retry_length'] or self.RETRY_LENGTH)
                if ruthless and not of_acceptable_length:
                    ruthless = False
                    continue # try again
                else:
                    return Summary(confidence, cleaned_article)
        except StandardError, e:
            #logging.exception('error getting summary: ' + str(traceback.format_exception(*sys.exc_info())))
            logging.exception('error getting summary: ' )
            raise Unparseable(str(e)), None, sys.exc_info()[2]

    def get_article(self, candidates, best_candidate):
        # Now that we have the top candidate, look through its siblings for content that might also be related.
        # Things like preambles, content split by ads that we removed, etc.

        sibling_score_threshold = max([10, best_candidate['content_score'] * 0.2])
        article = B.DIV()
        article.attrib['id'] = 'article'
        best_elem = best_candidate['elem']
        for sibling in best_elem.getparent().getchildren():
            #if isinstance(sibling, NavigableString): continue#in lxml there no concept of simple text 
            append = False 
            if sibling is best_elem:
                append = True
            sibling_key = sibling #HashableElement(sibling)

            # Print out sibling information for debugging.
            if sibling_key in candidates:
                sibling_candidate = candidates[sibling_key]
                self.debug(
                        "Sibling: %6.3f %s" %
                        (sibling_candidate['content_score'], describe(sibling))
                        )
            else:
                self.debug("Sibling: %s" % describe(sibling))

            if sibling_key in candidates and candidates[sibling_key]['content_score'] >= sibling_score_threshold:
                append = True

            if sibling.tag == "p":
                link_density = self.get_link_density(sibling)
                node_content = sibling.text or ""
                node_length = len(node_content)

                if node_length > 80 and link_density < 0.25:
                    append = True
                elif node_length < 80 and link_density == 0 and re.search('\.( |$)', node_content):
                    append = True

            if append:
                article.append(sibling)

        #if article is not None: 
        #    article.append(best_elem)
        return article

    def select_best_candidate(self, candidates):
        sorted_candidates = sorted(candidates.values(), key=lambda x: x['content_score'], reverse=True)
        for candidate in sorted_candidates[:5]:
            elem = candidate['elem']
            self.debug("Top 5 : %6.3f %s" % (candidate['content_score'], describe(elem)))

        if len(sorted_candidates) == 0:
            return None

        best_candidate = sorted_candidates[0]
        return best_candidate


    def get_link_density(self, elem):
        link_length = 0
        for i in elem.findall(".//a"):
            link_length += text_length(i)
        #if len(elem.findall(".//div") or elem.findall(".//p")):
        #    link_length = link_length
        total_length = text_length(elem)
        return float(link_length) / max(total_length, 1)

    def score_paragraphs(self, ):
        MIN_LEN = self.options.get('min_text_length', self.TEXT_LENGTH_THRESHOLD)
        candidates = {}
        #self.debug(str([describe(node) for node in self.tags(self.html, "div")]))

        ordered = []
        for elem in self.tags(self.html, "p", "pre", "td"):
            self.debug('Scoring %s' % describe(elem))
            parent_node = elem.getparent()
            if parent_node is None:
                continue 
            grand_parent_node = parent_node.getparent()

            inner_text = clean(elem.text_content() or "")
            inner_text_len = len(inner_text)

            # If this paragraph is less than 25 characters, don't even count it.
            if inner_text_len < MIN_LEN:
                continue

            if parent_node not in candidates:
                candidates[parent_node] = self.score_node(parent_node)
                ordered.append(parent_node)
                
            if grand_parent_node is not None and grand_parent_node not in candidates:
                candidates[grand_parent_node] = self.score_node(grand_parent_node)
                ordered.append(grand_parent_node)

            content_score = 1
            content_score += len(inner_text.split(','))
            content_score += min((inner_text_len / 100), 3)
            #if elem not in candidates:
            #    candidates[elem] = self.score_node(elem)
                
            #WTF? candidates[elem]['content_score'] += content_score
            candidates[parent_node]['content_score'] += content_score
            if grand_parent_node is not None:
                candidates[grand_parent_node]['content_score'] += content_score / 2.0

        # Scale the final candidates score based on link density. Good content should have a
        # relatively small link density (5% or less) and be mostly unaffected by this operation.
        for elem in ordered:
            candidate = candidates[elem]
            ld = self.get_link_density(elem)
            score = candidate['content_score']
            self.debug("Candid: %6.3f %s link density %.3f -> %6.3f" % (score, describe(elem), ld, score*(1-ld)))
            candidate['content_score'] *= (1 - ld)

        return candidates

    def class_weight(self, e):
        weight = 0
        if e.get('class', None):
            if REGEXES['negativeRe'].search(e.get('class')):
                weight -= 25

            if REGEXES['positiveRe'].search(e.get('class')):
                weight += 25

        if e.get('id', None):
            if REGEXES['negativeRe'].search(e.get('id')):
                weight -= 25

            if REGEXES['positiveRe'].search(e.get('id')):
                weight += 25

        return weight

    def score_node(self, elem):
        content_score = self.class_weight(elem)
        name = elem.tag.lower()
        if name == "div":
            content_score += 5
        elif name in ["pre", "td", "blockquote"]:
            content_score += 3
        elif name in ["address", "ol", "ul", "dl", "dd", "dt", "li", "form"]:
            content_score -= 3
        elif name in ["h1", "h2", "h3", "h4", "h5", "h6", "th"]:
            content_score -= 5
        return { 
            'content_score': content_score, 
            'elem': elem
        }

    def debug(self, *a):
        #if self.options['debug']:
            logging.debug(*a)

    def remove_unlikely_candidates(self):
        for elem in self.html.iter():
            s = "%s %s" % (elem.get('class', ''), elem.get('id', ''))
            #self.debug(s)
            if (REGEXES['unlikelyCandidatesRe'].search(s) and
                    (not REGEXES['okMaybeItsACandidateRe'].search(s)) and
                    elem.tag != 'body' and
                    elem.getparent() is not None
                    ):
                self.debug("Removing unlikely candidate - %s" % describe(elem))
                elem.drop_tree()

    def transform_misused_divs_into_paragraphs(self):
        for elem in self.tags(self.html, 'div'):
            # transform <div>s that do not contain other block elements into <p>s
            if not REGEXES['divToPElementsRe'].search(unicode(''.join(map(tostring, list(elem))))):
                self.debug("Altering %s to p" % (describe(elem)))
                elem.tag = "p"
                #print "Fixed element "+describe(elem)
                
        for elem in self.tags(self.html, 'div'):
            if elem.text and elem.text.strip():
                p = fragment_fromstring('<p/>')
                p.text = elem.text
                elem.text = None
                elem.insert(0, p)
                self.debug("Appended %s to %s" % (tounicode(p), describe(elem)))
                #print "Appended "+tounicode(p)+" to "+describe(elem)
            
            for pos, child in reversed(list(enumerate(elem))):
                if child.tail and child.tail.strip():
                    p = fragment_fromstring('<p/>')
                    p.text = child.tail
                    child.tail = None
                    elem.insert(pos + 1, p)
                    self.debug("Inserted %s to %s" % (tounicode(p), describe(elem)))
                    #print "Inserted "+tounicode(p)+" to "+describe(elem)
                if child.tag == 'br':
                    #print 'Dropped <br> at '+describe(elem) 
                    child.drop_tree()

    def findNextPageLink(self, elem):
        allLinks = self.tags(elem, ['a'])
        baseUrl = self.find_base_url(self.options['url'])

    def tags(self, node, *tag_names):
        for tag_name in tag_names:
            for e in node.findall('.//%s' % tag_name):
                yield e

    def reverse_tags(self, node, *tag_names):
        for tag_name in tag_names:
            for e in reversed(node.findall('.//%s' % tag_name)):
                yield e

    def sanitize(self, node, candidates):
        MIN_LEN = self.options.get('min_text_length', self.TEXT_LENGTH_THRESHOLD)
        for header in self.tags(node, "h1", "h2", "h3", "h4", "h5", "h6"):
            if self.class_weight(header) < 0 or self.get_link_density(header) > 0.33: 
                header.drop_tree()

        for elem in self.tags(node, "form", "iframe", "textarea"):
            elem.drop_tree()
        allowed = {}
        # Conditionally clean <table>s, <ul>s, and <div>s
        for el in self.reverse_tags(node, "table", "ul", "div"):
            if el in allowed:
                continue
            weight = self.class_weight(el)
            if el in candidates:
                content_score = candidates[el]['content_score']
                #print '!',el, '-> %6.3f' % content_score
            else:
                content_score = 0
            tag = el.tag

            if weight + content_score < 0:
                self.debug("Cleaned %s with score %6.3f and weight %-3s" %
                    (describe(el), content_score, weight, ))
                el.drop_tree()
            elif el.text_content().count(",") < 10:
                counts = {}
                for kind in ['p', 'img', 'li', 'a', 'embed', 'input']:
                    counts[kind] = len(el.findall('.//%s' %kind))
                counts["li"] -= 100

                content_length = text_length(el) # Count the text length excluding any surrounding whitespace
                link_density = self.get_link_density(el)
                parent_node = el.getparent()
                if parent_node is not None:
                    if parent_node in candidates:
                        content_score = candidates[parent_node]['content_score']
                    else:
                        content_score = 0
                #if parent_node is not None:
                    #pweight = self.class_weight(parent_node) + content_score
                    #pname = describe(parent_node)
                #else:
                    #pweight = 0
                    #pname = "no parent"
                to_remove = False
                reason = ""

                #if el.tag == 'div' and counts["img"] >= 1:
                #    continue
                if counts["p"] and counts["img"] > counts["p"]:
                    reason = "too many images (%s)" % counts["img"]
                    to_remove = True
                elif counts["li"] > counts["p"] and tag != "ul" and tag != "ol":
                    reason = "more <li>s than <p>s"
                    to_remove = True
                elif counts["input"] > (counts["p"] / 3):
                    reason = "less than 3x <p>s than <input>s"
                    to_remove = True
                elif content_length < (MIN_LEN) and (counts["img"] == 0 or counts["img"] > 2):
                    reason = "too short content length %s without a single image" % content_length
                    to_remove = True
                elif weight < 25 and link_density > 0.2:
                        reason = "too many links %.3f for its weight %s" % (link_density, weight)
                        to_remove = True
                elif weight >= 25 and link_density > 0.5:
                    reason = "too many links %.3f for its weight %s" % (link_density, weight)
                    to_remove = True
                elif (counts["embed"] == 1 and content_length < 75) or counts["embed"] > 1:
                    reason = "<embed>s with too short content length, or too many <embed>s"
                    to_remove = True
#                if el.tag == 'div' and counts['img'] >= 1 and to_remove:
#                    imgs = el.findall('.//img')
#                    valid_img = False
#                    self.debug(tounicode(el))
#                    for img in imgs:
#
#                        height = img.get('height')
#                        text_length = img.get('text_length')
#                        self.debug ("height %s text_length %s" %(repr(height), repr(text_length)))
#                        if to_int(height) >= 100 or to_int(text_length) >= 100:
#                            valid_img = True
#                            self.debug("valid image" + tounicode(img))
#                            break
#                    if valid_img:
#                        to_remove = False
#                        self.debug("Allowing %s" %el.text_content())
#                        for desnode in self.tags(el, "table", "ul", "div"):
#                            allowed[desnode] = True

                    #find x non empty preceding and succeeding siblings
                    i, j = 0, 0
                    x  = 1
                    siblings = []
                    for sib in el.itersiblings():
                        #self.debug(sib.text_content())
                        sib_content_length = text_length(sib)
                        if sib_content_length:
                            i =+ 1
                            siblings.append(sib_content_length)
                            if i == x:
                                break
                    for sib in el.itersiblings(preceding=True):
                        #self.debug(sib.text_content())
                        sib_content_length = text_length(sib)
                        if sib_content_length:
                            j =+ 1
                            siblings.append(sib_content_length)
                            if j == x:
                                break
                    #self.debug(str(siblings))
                    if siblings and sum(siblings) > 1000 :
                        to_remove = False
                        self.debug("Allowing %s" % describe(el))
                        for desnode in self.tags(el, "table", "ul", "div"):
                            allowed[desnode] = True

                if to_remove:
                    self.debug("Cleaned %6.3f %s with weight %s cause it has %s." %
                        (content_score, describe(el), weight, reason))
                    #print tounicode(el)
                    #self.debug("pname %s pweight %.3f" %(pname, pweight))
                    el.drop_tree()

        for el in ([node] + [n for n in node.iter()]):
            if not (self.options['attributes']):
                #el.attrib = {} #FIXME:Checkout the effects of disabling this
                pass

        return clean_attributes(tounicode(node))
    

class HashableElement():
    def __init__(self, node):
        self.node = node
        self._path = None

    def _get_path(self):
        if self._path is None:
            reverse_path = []
            node = self.node
            while node is not None:
                node_id = (node.tag, tuple(node.attrib.items()), node.text)
                reverse_path.append(node_id)
                node = node.getparent()
            self._path = tuple(reverse_path)
        return self._path
    path = property(_get_path)

    def __hash__(self):
        return hash(self.path)

    def __eq__(self, other):
        return self.path == other.path

    def __getattr__(self, tag):
        return getattr(self.node, tag)

class TestFindBaseUrl(unittest.TestCase):

    def setUp(self):
        self.longMessage = True

    def _assert_url(self, url, expected_base_url, msg = None):
        actual_base_url = find_base_url(url)
        self.assertEqual(expected_base_url, actual_base_url, msg)

    def _run_urls(self, specs):
        """
        Asserts expected results on a sequence of specs, where each spec is a
        pair: (URL, expected base URL).
        """
        for spec in specs:
            url = spec[0]
            expected = spec[1]
            if len(spec) > 2:
                msg = spec[2]
            else:
                msg = None
            self._assert_url(url, expected, msg)

    def test_none(self):
        self._assert_url(None, None)

    def test_no_change(self):
        url = 'http://foo.com/article'
        self._assert_url(url, url)

    def test_extension_stripping(self):
        specs = [
                (
                'http://foo.com/article.html',
                'http://foo.com/article',
                'extension should be stripped'
                ),
                (
                'http://foo.com/path/to/article.html',
                'http://foo.com/path/to/article',
                'extension should be stripped'
                ),
                (
                'http://foo.com/article.123not',
                'http://foo.com/article.123not',
                '123not is not extension'
                ),
                (
                'http://foo.com/path/to/article.123not',
                'http://foo.com/path/to/article.123not',
                '123not is not extension'
                )
                ]
        self._run_urls(specs)

    def test_ewcms(self):
        self._assert_url(
                'http://www.ew.com/ew/article/0,,20313460_20369436,00.html',
                'http://www.ew.com/ew/article/0,,20313460_20369436'
                )

    def test_page_numbers(self):
        specs = [
                (
                'http://foo.com/page5.html',
                'http://foo.com',
                'page number should be stripped'
                ),
                (
                'http://foo.com/path/to/page5.html',
                'http://foo.com/path/to',
                'page number should be stripped'
                ),
                (
                'http://foo.com/article-5.html',
                'http://foo.com/article',
                'page number should be stripped'
                )
                ]
        self._run_urls(specs)

    def test_numbers(self):
        specs = [
                (
                'http://foo.com/5.html',
                'http://foo.com',
                'number should be stripped'
                ),
                (
                'http://foo.com/path/to/5.html',
                'http://foo.com/path/to',
                'number should be stripped'
                )
                ]
        self._run_urls(specs)

    def test_index(self):
        specs = [
                (
                'http://foo.com/index.html',
                'http://foo.com',
                'index should be stripped'
                ),
                (
                'http://foo.com/path/to/index.html',
                'http://foo.com/path/to',
                'index should be stripped'
                )
                ]
        self._run_urls(specs)

def readability_main():
    from optparse import OptionParser
    parser = OptionParser(usage="%prog: [options] [file]")
    parser.add_option('-v', '--verbose', action='store_true')
    parser.add_option('-u', '--url', help="use URL instead of a local file")
    (options, args) = parser.parse_args()
    
    if not (len(args) == 1 or options.url):
        parser.print_help()
        sys.exit(1)
    logging.basicConfig(level=logging.INFO)

    file = None
    if options.url:
        import urllib
        file = urllib.urlopen(options.url)
    else:
        file = open(args[0])
    try:
        print Document(file.read(), debug=options.verbose).summary().html
    finally:
        file.close()

def main():
    if len(sys.argv) == 2 and sys.argv[1] == 'test':
        del sys.argv[1]
        unittest.main()
    else:
        readability_main()

if __name__ == '__main__':
    main()
