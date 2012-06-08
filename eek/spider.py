import urlparse
import csv
import sys
import re
import collections
import time
import requests

from eek import robotparser  # this project's version
from eek.BeautifulSoup import BeautifulSoup

from lxml import html


encoding_re = re.compile("charset\s*=\s*(\S+?)(;|$)")
html_re = re.compile("text/html")
def encoding_from_content_type(content_type):
    """
    Extracts the charset from a Content-Type header.

    >>> encoding_from_content_type('text/html; charset=utf-8')
    'utf-8'
    >>> encoding_from_content_type('text/html')
    >>>
    """
    if not content_type:
        return None
    match = encoding_re.search(content_type)
    return match and match.group(1) or None


class NotHtmlException(Exception):
    pass


class UrlTask(tuple):
    """
    We need to keep track of referers, but we don't want to add a url multiple
    times just because it was referenced on multiple pages
    """
    def __hash__(self):
        return hash(self[0])
    def __eq__(self, other):
        return self[0] == other[0]


class VisitOnlyOnceClerk(object):
    def __init__(self):
        self.visited = set()
        self.to_visit = set()
    def enqueue(self, url, referer):
        if not url in self.visited:
            self.to_visit.add(UrlTask((url, referer)))
    def __bool__(self):
        return bool(self.to_visit)
    def __iter__(self):
        while self.to_visit:
            (url, referer) = self.to_visit.pop()
            self.visited.add(url)
            yield (url, referer)


def lremove(string, prefix):
    """
    Remove a prefix from a string, if it exists.
    >>> lremove('www.foo.com', 'www.')
    'foo.com'
    >>> lremove('foo.com', 'www.')
    'foo.com'
    """
    if string.startswith(prefix):
        return string[len(prefix):]
    else:
        return string


def beautify(response):
    if not response.content:
        raise NotHtmlException
    return html.fromstring(response.content)


def get_links(response):
    try:
        html = beautify(response)
        return [urlparse.urldefrag(urlparse.urljoin(response.url, i))[0] for i in html.xpath('//a/@href')]
    except NotHtmlException:
        return []


def force_bytes(str_or_unicode):
    if isinstance(str_or_unicode, unicode):
        return str_or_unicode.encode('utf-8')
    else:
        return str_or_unicode


def get_pages(base, clerk):
    clerk.enqueue(base, base)
    base_domain = lremove(urlparse.urlparse(base).netloc, 'www.')
    for (url, referer) in clerk:
        url = force_bytes(url)
        referer = force_bytes(referer)
        response = requests.get(
                url,
                headers={'Referer': referer, 'User-Agent': 'Fusionbox spider'},
                allow_redirects=False)
        for link in get_links(response):
            parsed = urlparse.urlparse(link)
            if lremove(parsed.netloc, 'www.') == base_domain:
                clerk.enqueue(link, url)
        yield referer, response


def metadata_spider(base, output=sys.stdout, delay=0):
    if not urlparse.urlparse(base).scheme:
        base = 'http://' + base
    writer = csv.writer(output)
    robots = robotparser.RobotFileParser(base + '/robots.txt')
    robots.read()
    writer.writerow(['url', 'title', 'description', 'keywords', 'allow', 'disallow',
                     'noindex', 'meta robots', 'canonical', 'referer', 'status'])

    for referer, response in get_pages(base, VisitOnlyOnceClerk()):
        rules = applicable_robot_rules(robots, response.url)

        results = collections.defaultdict(str)
        try:
            html = beautify(response)
            paths = {
                    'robots_meta': '//meta[@name="robots"]/@content',
                    'canonical': '//link[@rel="canonical"]/@href',
                    'title': '//head//title/text()',
                    'description': '//head//meta[@name="description"]/@content',
                    'keywords': '//head//meta[@name="keywords"]/@content',
                    }

            for key in paths:
                results[key] = ','.join(html.xpath(paths[key]))

        except NotHtmlException:
            pass

        writer.writerow(map(force_bytes, [
            response.url,
            results['title'],
            results['description'],
            results['keywords'],
            ','.join(rules['allow']),
            ','.join(rules['disallow']),
            ','.join(rules['noindex']),
            results['robots_meta'],
            results['canonical'],
            referer,
            response.status_code,
            ]))
        if delay:
            time.sleep(delay)


def graphviz_spider(base, delay=0):
    print "digraph links {"
    for referer, response in get_pages(base, VisitOnlyOnceClerk()):
        for link in get_links(response):
            print '  "%s" -> "%s";' % (force_bytes(response.url), force_bytes(link))
            if delay:
                time.sleep(delay)
    print "}"


def applicable_robot_rules(robots, url):
    rules = collections.defaultdict(list)
    if robots.default_entry:
        rules[robots.default_entry.allowance(url)].append('*')
    for entry in robots.entries:
        rules[entry.allowance(url)].extend(entry.useragents)
    return rules
