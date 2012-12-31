# -*- coding: utf-8 -*-
import os
import re
import urllib2

from flask import Flask, Response, request
from flask.ext.restful import (abort, Api, fields, marshal, marshal_with,
                               Resource)
from flask.ext.sqlalchemy import SQLAlchemy
import translitcodec
import requests

NUM_RE = re.compile('\d')
PUNCT_RE = re.compile(r'[\t !"#$%&\'()*\-/<=>?@\[\\\]^_`{|},.]+')

# Setup {{{
app = Flask(__name__)
DB_PATH = 'shiva.db'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///%s' % DB_PATH
db = SQLAlchemy(app)
api = Api(app)
# }}}


# Utils {{{
def slugify(text):
    """Generates an ASCII-only slug."""
    result = []
    for word in PUNCT_RE.split(text.lower()):
        word = word.encode('translit/long')
        if word:
            result.append(word)

    return unicode(u'-'.join(result))


class ID3Manager(object):
    def __init__(self, mp3_path):
        import eyed3  # FIXME: Replace ASAP

        self.mp3_path = mp3_path
        self.reader = eyed3.load(mp3_path)

        if not self.reader.tag:
            self.reader.tag = eyed3.id3.Tag()
            self.reader.tag.save(mp3_path)

    def __getattribute__(self, attr):
        _super = super(ID3Manager, self)
        try:
            _getter = _super.__getattribute__('get_%s' % attr)
        except AttributeError:
            _getter = None
        if _getter:
            return _getter()

        return super(ID3Manager, self).__getattribute__(attr)

    def __setattr__(self, attr, value):
        value = value.strip() if isinstance(value, (str, unicode)) else value
        _setter = getattr(self, 'set_%s' % attr, None)
        if _setter:
            _setter(value)

        super(ID3Manager, self).__setattr__(attr, value)

    def is_valid(self):
        if not self.reader.path:
            return False

        return True

    def get_path(self):
        return self.mp3_path

    def same_path(self, path):
        return path == self.mp3_path

    def get_artist(self):
        return self.reader.tag.artist.strip()

    def set_artist(self, name):
        self.reader.tag.artist = name
        self.reader.tag.save()

    def get_album(self):
        return self.reader.tag.album.strip()

    def set_album(self, name):
        self.reader.tag.album = name
        self.reader.tag.save()

    def get_release_year(self):
        rdate = self.reader.tag.release_date
        return rdate.year if rdate else None

    def set_release_year(self, year):
        self.release_date.year = year
        self.reader.tag.save()

    def get_bitrate(self):
        return self.reader.info.bit_rate[1]

    def get_length(self):
        return self.reader.info.time_secs

    def get_track_number(self):
        return self.reader.tag.track_num[0]

    def get_title(self):
        if not self.reader.tag.title:
            _title = raw_input('Song title: ').decode('utf-8').strip()
            self.reader.tag.title = _title
            self.reader.tag.save()

        return self.reader.tag.title

    def get_size(self):
        """Computes the size of the mp3 file in filesystem.
        """
        return os.stat(self.reader.path).st_size
# }}}


# DB {{{
class Artist(db.Model):
    """
    """

    __tablename__ = 'artists'

    pk = db.Column(db.Integer, primary_key=True)
    # TODO: Update the files' ID3 tags when changing this info.
    name = db.Column(db.String(128), nullable=False)
    image = db.Column(db.String(256))
    slug = db.Column(db.String(), nullable=False)

    tracks = db.relationship('Track', backref='artist', lazy='dynamic')

    def __setattr__(self, attr, value):
        if attr == 'name':
            super(Artist, self).__setattr__('slug', slugify(value))

        super(Artist, self).__setattr__(attr, value)

    def __repr__(self):
        return '<Artist (%s)>' % self.name


artists = db.Table('albumartists',
    db.Column('artist_pk', db.Integer, db.ForeignKey('artists.pk')),
    db.Column('album_pk', db.Integer, db.ForeignKey('albums.pk'))
)


class Album(db.Model):
    """
    """

    __tablename__ = 'albums'

    pk = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), unique=True)
    year = db.Column(db.Integer)
    cover = db.Column(db.String(256))
    slug = db.Column(db.String(), nullable=False)

    tracks = db.relationship('Track', backref='album', lazy='dynamic')

    artists = db.relationship('Artist', secondary=artists,
                              backref=db.backref('albums', lazy='dynamic'))

    def __setattr__(self, attr, value):
        if attr == 'name':
            super(Album, self).__setattr__('slug', slugify(value))

        super(Album, self).__setattr__(attr, value)

    def __repr__(self):
        return '<Album (%s)>' % self.name


class Track(db.Model):
    """
    """

    __tablename__ = 'tracks'

    pk = db.Column(db.Integer, primary_key=True)
    path = db.Column(db.Unicode(256), unique=True, nullable=False)
    title = db.Column(db.String(128))
    bitrate = db.Column(db.Integer)
    file_size = db.Column(db.Integer)
    length = db.Column(db.Integer)
    number = db.Column(db.Integer)
    slug = db.Column(db.String(), nullable=False)

    album_pk = db.Column(db.Integer, db.ForeignKey('albums.pk'))
    artist_pk = db.Column(db.Integer, db.ForeignKey('artists.pk'))

    def __init__(self, path):
        if type(path) not in (unicode, str, file):
            raise ValueError('Invalid parameter for Track. Path or File '
                             'expected, got %s' % type(path))

        _path = path
        if isinstance(path, file):
            _path = path.name

        self.set_path(_path)
        self._id3r = None

    def __setattr__(self, attr, value):
        if attr == 'title':
            super(Track, self).__setattr__('slug', slugify(value))

        super(Track, self).__setattr__(attr, value)

    def get_path(self):
        if self.path:
            return self.path.encode('utf-8')

        return None

    def set_path(self, path):
        if path != self.get_path():
            self.path = path
            if os.path.exists(self.get_path()):
                self.file_size = self.get_id3_reader().size
                self.bitrate = self.get_id3_reader().bitrate
                self.length = self.get_id3_reader().length
                self.number = self.get_id3_reader().track_number
                self.title = self.get_id3_reader().title

    def get_id3_reader(self):
        """Returns an object with the ID3 info reader.
        """
        if not getattr(self, '_id3r', None):
            self._id3r = ID3Manager(self.get_path())

        return self._id3r

    def __repr__(self):
        return "<Track('%s')>" % self.title
# }}}


# Fields {{{
class FieldMap(fields.Raw):
    def __init__(self, field_name, formatter):
        self.field_name = field_name
        self.formatter = formatter

    def format(self, value):
        self.formatter(value)

    def output(self, key, obj):
        return getattr(obj, self.field_name)


class InstanceURI(fields.String):
    def __init__(self, base_uri):
        self.base_uri = base_uri

    def output(self, key, obj):
        return '/%s/%i' % (self.base_uri, obj.pk)


class StreamURI(InstanceURI):
    def output(self, key, obj):
        uri = super(StreamURI, self).output(key, obj)

        return '%s/stream' % uri


class DownloadURI(InstanceURI):
    def output(self, key, obj):
        uri = super(DownloadURI, self).output(key, obj)

        return '%s/download' % uri


class ManyToManyField(fields.Raw):
    def __init__(self, foreign_obj, nested):
        self.foreign_obj = foreign_obj
        self.nested = nested

        super(ManyToManyField, self).__init__()

    def output(self, key, obj):
        items = list()
        for item in getattr(obj, key):
            items.append(marshal(item, self.nested))

        return items


class ForeignKeyField(fields.Raw):
    def __init__(self, foreign_obj, nested):
        self.foreign_obj = foreign_obj
        self.nested = nested

        super(ForeignKeyField, self).__init__()

    def output(self, key, obj):
        _id = getattr(obj, '%s_pk' % key)
        if not _id:
            return None

        obj = self.foreign_obj.query.get(_id)

        return marshal(obj, self.nested)


class AlbumCover(fields.Raw):
    def output(self, key, obj):
        output = super(AlbumCover, self).output(key, obj)
        if not output:
            output = ('http://wortraub.com/wp-content/uploads/2012/07/'
                     'Vinyl_Close_Up.jpg')

        return output
# }}}


# Resources {{{
class JSONResponse(Response):
    def __init__(self, status=200, **kwargs):
        params = {
            'headers': [],
            'mimetype': 'application/json',
            'response': '',
            'status': status,
        }
        params.update(kwargs)

        super(JSONResponse, self).__init__(**params)


class ArtistResource(Resource):
    """
    """

    route_base = 'artists'
    resource_fields = {
        'id': FieldMap('pk', lambda x: int(x)),
        'name': fields.String,
        'uri': InstanceURI('artist'),
        'download_uri': DownloadURI('artist'),
        'image': fields.String,
        'slug': fields.String,
    }

    def get(self, artist_id=None):
        if not artist_id:
            return list(self.get_all())

        return self.get_one(artist_id)

    def get_all(self):
        for artist in Artist.query.order_by(Artist.name):
            yield marshal(artist, self.resource_fields)

    def get_one(self, artist_id):
        artist = Artist.query.get(artist_id)

        if not artist:
            return JSONResponse(404)

        return marshal(artist, self.resource_fields)

    def post(self, artist_id=None):
        if artist_id:
            return JSONResponse(405)

        # artist = new Artist(name=request.form.get('name'))
        # artist.save()

        return JSONResponse(201, headers=[('Location', '/artist/1337')])

    def put(self, artist_id=None):
        if not artist_id:
            return JSONResponse(405)

        return {}

    def delete(self, artist_id=None):
        if not artist_id:
            return JSONResponse(405)

        artist = Artist.query.get(artist_id)
        if not artist:
            return JSONResponse(404)

        db.session.delete(artist)
        db.session.commit()

        return {}


class AlbumResource(Resource):
    """
    """

    route_base = 'albums'
    resource_fields = {
        'id': FieldMap('pk', lambda x: int(x)),
        'name': fields.String,
        'slug': fields.String,
        'year': fields.Integer,
        'uri': InstanceURI('album'),
        'artists': ManyToManyField(Artist, {
            'id': FieldMap('pk', lambda x: int(x)),
            'uri': InstanceURI('artist'),
        }),
        'download_uri': DownloadURI('album'),
        'cover': AlbumCover,
    }

    def get(self, album_id=None):
        if not album_id:
            return list(self.get_many())

        return self.get_one(album_id)

    def get_many(self):
        artist_pk = request.args.get('artist')
        if artist_pk:
            albums = Album.query.join(Album.artists).filter(
                Artist.pk == artist_pk)
        else:
            albums = Album.query

        for album in albums.order_by(Album.year, Album.name, Album.pk):
            yield marshal(album, self.resource_fields)

    @marshal_with(resource_fields)
    def get_one(self, album_id):
        album = Album.query.get(album_id)

        if not album:
            abort(404)

        return album

    def delete(self, album_id=None):
        if not album_id:
            return JSONResponse(405)

        album = Album.query.get(album_id)
        if not album:
            return JSONResponse(404)

        db.session.delete(album)
        db.session.commit()

        return {}


class TracksResource(Resource):
    """
    """

    route_base = 'tracks'
    resource_fields = {
        'id': FieldMap('pk', lambda x: int(x)),
        'uri': InstanceURI('track'),
        'download_uri': DownloadURI('track'),
        'bitrate': fields.Integer,
        'length': fields.Integer,
        'title': fields.String,
        'slug': fields.String,
        'artist': ForeignKeyField(Album, {
            'id': FieldMap('pk', lambda x: int(x)),
            'uri': InstanceURI('artist'),
        }),
        'album': ForeignKeyField(Album, {
            'id': FieldMap('pk', lambda x: int(x)),
            'uri': InstanceURI('album'),
        }),
        'number': fields.Integer,
    }

    def get(self, track_id=None):
        if not track_id:
            return list(self.get_many())

        return self.get_one(track_id)

    # TODO: Pagination
    def get_many(self):
        album_pk = request.args.get('album')
        artist_pk = request.args.get('artist')
        if album_pk:
            album_pk = None if album_pk == 'null' else album_pk
            tracks = Track.query.filter_by(album_pk=album_pk)
        elif artist_pk:
            tracks = Track.query.filter(Track.artist_pk == artist_pk)
        else:
            tracks = Track.query

        for track in tracks.order_by(Track.album_pk, Track.number, Track.pk):
            yield marshal(track, self.resource_fields)

    @marshal_with(resource_fields)
    def get_one(self, track_id):
        track = Track.query.get(track_id)

        if not track:
            abort(404)

        return track

    def delete(self, track_id=None):
        if not track_id:
            return JSONResponse(405)

        track = Track.query.get(track_id)
        if not track:
            return JSONResponse(404)

        db.session.delete(track)
        db.session.commit()

        return {}


class LyricsResource(Resource):
    """
    """

    def get(self, track_id):
        track = Track.query.get(track_id)
        lyricswiki = ('http://lyrics.wikia.com/api.php?'
                      'artist=%(artist)s&song=%(track)s&fmt=realjson')
        print(lyricswiki % {
            'artist': urllib2.quote(track.artist.name),
            'track': urllib2.quote(track.title),
        })
        response = requests.get(lyricswiki % {
            'artist': urllib2.quote(track.artist.name),
            'track': urllib2.quote(track.title),
        })
        lyrics = response.json().get('lyrics')
        if lyrics != "Not found":
            return {
                'lyrics': lyrics,
                'uri': response.json().get('url'),
                'artist': {
                    'id': track.artist.pk,
                    'uri': '/artist/%i' % track.artist.pk,
                },
                'track': {
                    'id': track.pk,
                    'uri': '/track/%i' % track.pk,
                },
            }

        return JSONResponse(404)


class ShowsResource(Resource):
    """
    """

    def get(self, artist_id):
        bit_uri = ('http://api.bandsintown.com/artists/%(artist)s/events.json?'
                   'api_version=2.0&app_id=MY_APP_ID&location=%(location)s')
        artist = Artist.query.get(artist_id)

        if not artist:
            return JSONResponse(404)

        print(bit_uri % {
            'artist': urllib2.quote(artist.name),
            'location': urllib2.quote('Berlin, Germany'),
        })
        response = requests.get(bit_uri % {
            'artist': urllib2.quote(artist.name),
            'location': urllib2.quote('Berlin, Germany'),
        })

        return JSONResponse(response=response.text,
                            status=response.status_code)


api.add_resource(ArtistResource, '/artists', '/artist/<int:artist_id>',
                 endpoint='artist')
api.add_resource(ShowsResource, '/artist/<int:artist_id>/shows',
                 endpoint='shows')

api.add_resource(AlbumResource, '/albums', '/album/<int:album_id>',
                 endpoint='album')

api.add_resource(TracksResource, '/tracks', '/track/<int:track_id>',
                 endpoint='track')
api.add_resource(LyricsResource, '/track/<int:track_id>/lyrics',
                 endpoint='lyrics')
# }}}


# Routes {{{
@app.route('/track/<int:track_id>/download.<ext>')
def download(track_id, ext):
    """
    """
    if ext != 'mp3':
        raise NotImplementedError

    track = Track.query.get(track_id)
    track_file = open(track.get_path(), 'r')
    filename_header = (
        'Content-Disposition', 'attachment; filename="%s.mp3"' % track.title
    )

    return Response(response=track_file.read(), mimetype='audio/mpeg',
            headers=[filename_header])
# }}}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
