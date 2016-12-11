#!/usr/bin/env python
# -*- coding: utf-8 -*-
import errno
import logging
import optparse
import os
import requests
import shutil
import sys
from Cheetah.Template import Template
from bs4 import BeautifulSoup
from urlparse import urlparse
from xml.etree import ElementTree

log = logging.getLogger(__name__)

Namespaces = {
    'atom': 'http://www.w3.org/2005/Atom',
    'gd': 'http://schemas.google.com/g/2005',
    'thr': 'http://purl.org/syndication/thread/1.0'
}


def get_alternate(entry):
    """
    Obtiene la url original de la entrada
    """
    for link in entry.findall('atom:link', Namespaces):
        if link.get('rel') == 'alternate':
            return link.get('href')


def get_category(entry):
    """
    Obtiene el tipo de entrada
    """
    for category in entry.findall('atom:category', Namespaces):
        if category.get('scheme') == 'http://schemas.google.com/g/2005#kind':
            return category.get('term')


def download(src, outdir):
    """
    Descarga src en el directorio outdir
    """
    parsed = urlparse(src)
    domain_dir = os.path.join(outdir, parsed.netloc)
    path = os.path.join(domain_dir, parsed.path[1:])
    parent = os.path.dirname(path)
    try:
        os.makedirs(parent)
        log.info("Mkdir %s" % parent)
    except OSError, e:
        if e.errno != errno.EEXIST:
            raise

    if not os.path.exists(path):
        try:
            log.info("Download %s", path)
            resp = requests.get(src)
            log.info("Descargado!")
            with open(path, 'wtc') as fd:
                fd.write(resp.content)
        except Exception, e:
            log.error(str(e))
    else:
        log.info("Ya existe %s" % path)
    return path


class Author(object):
    """
    Autor
    """

    def __init__(self, name, avatar):
        self.name = name
        self.avatar = avatar


class Comment(object):
    """
    Comentario
    """
    CATEGORY = 'http://schemas.google.com/blogger/2008/kind#comment'

    def __init__(self, author, published, raw_html):
        self.author = author
        self.published = published
        if raw_html:
            self.content = BeautifulSoup(raw_html, 'html.parser')
        else:
            self.content = BeautifulSoup("", 'html.parser')


class Post(object):
    """
    Entrada del blog
    """

    CATEGORY = 'http://schemas.google.com/blogger/2008/kind#post'

    def __init__(self, href, author, published, title, raw_html):
        self.href = href
        self.author = author
        self.published = published
        self.title = title
        if self.title is None:
            self.title = ""
        if raw_html:
            self.content = BeautifulSoup(raw_html, 'html.parser')
        else:
            self.content = BeautifulSoup("", 'html.parser')
        self.comments = []

    def filename(self):
        if not self.href:
            return '#'
        return os.path.basename(urlparse(self.href).path)

    def add_comment(self, comment):
        self.comments.append(comment)

    def comment_count(self):
        return len(self.comments)

    def _prepare_local(self, outdir):
        href_dict = {}
        for img in self.content.find_all('img'):
            try:
                src = img['src']
                full_path = download(src, outdir)
                # Path returned by download is relative to the script, not to the generated html!
                local_path = os.path.join(*(full_path.split(os.path.sep)[1:]))
                img['src'] = local_path
                # Store a dictionary to re-point links too
                href_dict[src] = local_path
            except Exception, e:
                log.warning("%s", str(e))

        # Replace links
        for a in self.content.find_all('a'):
            if a.has_attr('href'):
                href = a['href']
                extension = href.rsplit('.', 1)[-1]
                # Replace link to a known image
                if href in href_dict:
                    a['href'] = href_dict[href]
                # Otherwise, download it
                elif extension in ('jpg', 'jpeg', 'gif', 'bmp'):
                    full_path = download(href, outdir)
                    local_path = os.path.join(*(full_path.split(os.path.sep)[1:]))
                    a['href'] = local_path

    def store(self, outdir):
        self._prepare_local(os.path.join(outdir, 'images'))
        html = os.path.join(outdir, self.filename())
        template = Template(file='post.template.html', searchList=[self])
        with open(html, 'wct') as fd:
            fd.write(str(template))


class Blog(object):
    """
    Blog al completo
    """

    def _register_author(self, author_elm):
        author_name = author_elm.find('atom:name', Namespaces).text
        if author_name not in self.authors:
            avatar = author_elm.find('gd:image', Namespaces).get('src')
            self.authors[author_name] = Author(author_name, avatar)
            log.info("Nuevo autor %s con avatar %s", author_name, avatar)
        return self.authors[author_name]

    def _process_feed(self, atom):
        tree = ElementTree.parse(atom)

        self.title = tree.find('atom:title', Namespaces).text
        log.info("Titulo: %s" % self.title)

        for entry in tree.findall('atom:entry', Namespaces):
            title = entry.find('atom:title', Namespaces).text
            content = entry.find('atom:content', Namespaces).text
            category = get_category(entry)
            author = entry.find('atom:author', Namespaces)
            published = entry.find('atom:published', Namespaces).text

            author_obj = self._register_author(author)

            if category == Post.CATEGORY:
                href = get_alternate(entry)
                self.posts[href] = Post(href, author_obj, published, title, content)
                log.info("Nueva entrada '%s' %s" % (title, href))
            elif category == Comment.CATEGORY:
                href = entry.find('thr:in-reply-to', Namespaces).get('href')
                if href not in self.posts:
                    log.warning("Comentario sin entrada: %s" % href)
                    continue
                post = self.posts[href]
                post.add_comment(Comment(author_obj, published, content))
                log.info("Nuevo comentario para '%s'", post.title)
            else:
                log.warning("Categoría desconocida: %s" % category)

    def __init__(self, atom):
        self.posts = {}
        self.authors = {}
        self._process_feed(atom)

    def _generate_index(self, outdir):
        """
        Generar el index.html
        """
        template = Template(file='index.template.html', searchList=[dict(
            title=self.title,
            posts=sorted(self.posts.values(), key=lambda p: p.published)
        )])

        index = os.path.join(outdir, 'index.html')
        with open(index, 'wct') as fd:
            fd.write(str(template))

    def store(self, outdir):
        """
        Almacena localmente el blog
        """
        try:
            os.makedirs(outdir)
        except OSError, e:
            if e.errno != errno.EEXIST:
                raise

        shutil.copy(os.path.join(os.path.dirname(__file__), 'style.css'), outdir)

        self._generate_index(outdir)
        for post in self.posts.values():
            post.store(outdir)


if __name__ == '__main__':
    handler = logging.StreamHandler(sys.stdout)
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    parser = optparse.OptionParser()
    parser.add_option(
        '--clean', default=False, action='store_true',
        help='Limpiar directorio de destino'
    )
    parser.add_option(
        '--out', type=str, default='blog',
        help='Directorio donde almacenar el blog'
    )

    options, args = parser.parse_args()

    if len(args) != 1:
        parser.error("Se debe pasar el xml como parámetro")

    blog = Blog(args[0])
    if options.clean:
        log.warning("Limpiando directorio existente")
        try:
            shutil.rmtree(options.out)
        except Exception, e:
            log.warning(str(e))
    blog.store(options.out)
