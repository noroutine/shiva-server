# -*- coding: utf-8 -*-
# K-Pg
import os
import pickle
from datetime import datetime

import pylast

from shiva import models as m
from shiva.app import app, db
from shiva.utils import ID3Manager

q = db.session.query

class Indexer(object):
    def __init__(self, config=None):
        self.config = config
        self.media_dirs = config.get('MEDIA_DIRS', [])
        self.id3r = None
        self.PREV_ARTIST = None
        self.PREV_ALBUM = None
        self.lastfm = pylast.LastFMNetwork(api_key=config['LASTFM_API_KEY'])

        if len(self.media_dirs) == 0:
            print('Remember to set the MEDIA_DIRS setting, otherwise I '
                  'don\'t know where to look for.')

    def get_artist(self, name):
        artist = q(m.Artist).filter_by(name=name).first()
        if not artist:
            cover = self.lastfm.get_artist(name).get_cover_image()
            artist = m.Artist(name=name, image=cover)
            db.session.add(artist)

        return artist

    def get_release_year(self, lastfm_album):
        _date = lastfm_album.get_release_date()
        if not _date:
            if not self.get_id3_reader().release_year:
                return None

            return self.get_id3_reader().release_year

        return datetime.strptime(_date, '%d %b %Y, %H:%M').year

    def save_track(self):
        """Takes a path to a track, reads its metadata and stores everything in
        the database.
        """
        session = db.session
        full_path = self.file_path.decode('utf-8')

        print(self.file_path)

        if q(m.Track).filter_by(path=full_path).count():
            return True

        track = m.Track(full_path)

        use_prev = None
        id3r = self.get_id3_reader()
        if not id3r.artist:
            _prev = self.PREV_ARTIST
            if _prev:
                use_prev = raw_input('Use %s? [y/N] ' % _prev).strip()

            if use_prev == 'y':
                _artist = _prev
            else:
                _artist = unicode(raw_input('Artist name: ').strip())

            self.PREV_ARTIST = _artist
            id3r.artist = _artist

        use_prev = None
        if not id3r.album:
            _prev = self.PREV_ALBUM
            if _prev:
                use_prev = raw_input('Use %s? [y/N] ' % _prev).strip()

            if use_prev == 'y':
                _album = _prev
            else:
                _album = unicode(raw_input('Album name: ').strip())

            self.PREV_ALBUM = _album
            id3r.album = _album

        artist = self.get_artist(id3r.artist)

        album = q(m.Album).filter_by(name=id3r.album).first()
        if not album:
            _album = self.lastfm.get_album(self.lastfm.get_artist(artist.name),
                                           id3r.album)
            album = m.Album(name=id3r.album,
                            year=self.get_release_year(_album))
            album.cover = _album.get_cover_image(size=pylast.COVER_EXTRA_LARGE)

        if artist not in album.artists:
            album.artists.append(artist)

        session.add(album)

        track.album = album
        track.artist = artist
        session.add(track)

        session.commit()

        return True

    def get_id3_reader(self):
        if not self.id3r or not self.id3r.same_path(self.file_path):
            self.id3r = ID3Manager(self.file_path)

        return self.id3r

    def is_track(self):
        """Tries to guess whether the file is a valid track or not.
        """
        if os.path.isdir(self.file_path):
            return False

        if '.' not in self.file_path:
            return False

        ext = self.file_path[self.file_path.rfind('.') + 1:]
        if ext not in self.config.get('ACCEPTED_FORMATS', []):
            return False

        if not self.get_id3_reader().is_valid():
            return False

        return True

    def walk(self, dir_name):
        """Recursively walks through a directory looking for tracks.
        """

        if os.path.isdir(dir_name):
            for name in os.listdir(dir_name):
                self.file_path = os.path.join(dir_name, name)
                if os.path.isdir(self.file_path):
                    self.walk(self.file_path)
                else:
                    if self.is_track():
                        self.save_track()
        else:
            self.file_path = dir_name
            if self.is_track():
                self.save_track()

        return True

    def run(self):
        for mobject in self.media_dirs:
            for mdir in mobject.get_dirs():
                self.walk(mdir)

if __name__ == '__main__':
    lola = Indexer(app.config)
    lola.run()
