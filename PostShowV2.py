#!/usr/bin/env python3
"""Run conversion and tagging tasks for XBN shows."""

import os
# import urwid
import signal
import argparse
import threading
import subprocess
import mutagen.id3
import mutagen.mp3
import configparser


REQUIRED_TEXT_KEYS = ['slug', 'filename', 'bitrate', 'title', 'album',
                      'artist', 'season', 'language', 'genre']
REQUIRED_BOOL_KEYS = ['write_date', 'write_trackno', 'lyrics_equals_comment']


class MP3Tagger:
    """Tag an MP3."""
    def __init__(self, path: str):
        """Create a new tagger."""
        self.path = path
        # Create an ID3 tag if none exists
        try:
            self.tag = mutagen.id3.ID3(path)
        except mutagen.MutagenError:
            broken = mutagen.id3.ID3FileType(path)
            broken.add_tags(ID3=mutagen.id3.ID3)
            self.tag = broken.ID3()
        # Determine the length of the MP3 and write it to a TLEN frame
        mp3 = mutagen.mp3.MP3(path)
        length = round(mp3.info.length * 1000, 0)
        self.tag.add(mutagen.id3.TLEN(text=str(length)))

    def set_title(self, title: str) -> None:
        """Set the title of the MP3."""
        self.tag.delall('TIT2')
        self.tag.add(mutagen.id3.TIT2(text=title))

    def set_artist(self, artist: str) -> None:
        """Set the artist of the MP3."""
        self.tag.delall('TPE1')
        self.tag.add(mutagen.id3.TPE1(text=artist))

    def set_album(self, album: str) -> None:
        """Set the album of the MP3."""
        self.tag.delall('TALB')
        self.tag.add(mutagen.id3.TALB(text=album))

    def set_season(self, season: str) -> None:
        """Set the season of the MP3."""
        self.tag.delall('TPOS')
        self.tag.add(mutagen.id3.TPOS(text=season))

    def set_genre(self, genre: str) -> None:
        """Set the genre of the MP3."""
        self.tag.delall('TCON')
        self.tag.add(mutagen.id3.TCON(text=genre))

    def set_composer(self, composer: str) -> None:
        """Set the composer of the MP3."""
        self.tag.delall('TCOM')
        self.tag.add(mutagen.id3.TCOM(text=composer))

    def set_accompaniment(self, accompaniment: str) -> None:
        """Set the accompaniment of the MP3."""
        self.tag.delall('TPE2')
        self.tag.add(mutagen.id3.TPE2(text=accompaniment))

    def set_date(self, year: str) -> None:
        """Set the date of recording of the MP3."""
        self.tag.delall('TDRC')
        self.tag.add(mutagen.id3.TDRC(text=year))

    def set_trackno(self, trackno: str) -> None:
        """Set the track number of the MP3."""
        self.tag.delall('TRCK')
        self.tag.add(mutagen.id3.TRCK(text=trackno))

    def set_language(self, language: str) -> None:
        """Set the language of the MP3."""
        self.tag.delall('TLAN')
        self.tag.add(mutagen.id3.TLAN(text=language))

    def add_comment(self, lang: str, desc: str, comment: str) -> None:
        """Add a comment to the MP3."""
        self.tag.add(mutagen.id3.COMM(lang=lang, desc=desc, text=comment))

    def add_lyrics(self, lang: str, desc: str, lyrics: str) -> None:
        """Add lyrics to the MP3."""
        self.tag.add(mutagen.id3.USLT(lang=lang, desc=desc, text=lyrics))


class MP3Encoder(threading.Thread):
    """Shell out to LAME to encode the WAV file as an MP3."""

    def setup(self, infile: str, outfile: str, bitrate: str):
        """Configure the input and output files, and the encoder bitrate.

        :param infile: Path to WAV file.
        :param outfile: Path to create MP3 file at.
        :param bitrate: LAME CBR bitrate, in Kbps.
        """
        self.infile = infile
        self.outfile = outfile
        self.bitrate = bitrate

    def run(self):
        self.p = subprocess.Popen(['lame', '-t', '-b', self.bitrate, '--cbr',
                                  self.infile, self.outfile],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        # Do whatever is necessary to watch progress from lame
        self.p.wait()

    def request_stop(self):
        self.p.terminate()


class Chapter(object):
    """A podcast chapter."""

    def __init__(self, start: int, end: int, url=None, image=None, text=None,
                 indexed=True):
        """Create a new Chapter.

        :param start: The start time of the chapter, in milliseconds.
        :param end: The end time of the chapter, in milliseconds.
        :param url: An optional URL to include in the chapter.
        :param image: An optional path to an image, which will be read and
        embedded in the chapter.
        :param text: An optional string description of the chapter.
        :param indexed: Whether to include this chapter in the Table of
        Contents.
        """
        self.elem_id = None
        self.text = text
        self.start = start
        self.end = end
        self.url = url
        self.image = image
        self.indexed = indexed

    def as_chap(self) -> mutagen.id3.CHAP:
        """Convert this object into a mutagen CHAP object."""
        sub_frames = []
        if self.text is not None:
            sub_frames.append(mutagen.id3.TIT2(text=self.text))
        if self.url is not None:
            sub_frames.append(mutagen.id3.WXXX(
                desc='chapter url',
                url=self.url))
        if self.image is not None:
            raise NotImplementedError("I haven't done this bit yet.")
        return mutagen.id3.CHAP(
            element_id=self.elem_id,
            start_time=self.start,
            end_time=self.end,
            sub_frames=sub_frames
        )


class EpisodeMetadata(object):
    """Metadata about an episode."""
    def __init__(self, number: str, name: str, comment: str):
        self.number = number
        self.name = name
        self.comment = comment


class PostShowError(Exception):
    """Something went wrong, use this to explain."""


class Main:
    """Main object."""

    def __init__(self):
        """Setup tasks."""
        self.m = MP3Encoder()

        def exit_handler(sig, frame):
            m.request_stop()
        signal.signal(signal.SIGINT, exit_handler)
        self.args = self.parse_args()
        self.config = self.check_config(self.args.config)

    def ask_metadata(self) -> EpisodeMetadata:
        """Ask the user for metadata about the episode."""
        ep_num = None
        ep_name = None
        ep_comment = ""
        while ep_num is None:
            i = input("Episode number: ")
            if i != "":
                ep_num = i
        while ep_name is None:
            i = input("Episode name: ")
            if i != "":
                ep_name = i
        print("Episode comment (multiple lines OK, enter empty line to "
              "finish):")
        while True:
            i = input("> ")
            if i == "":
                break
            if ep_comment != "":
                ep_comment += "\r\n"
            ep_comment += i
        return EpisodeMetadata(ep_num, ep_name, ep_comment)

    @staticmethod
    def parse_args() -> argparse.Namespace:
        """Parse arguments to this program."""
        parser = argparse.ArgumentParser(description="Convert and tag WAVs and"
                                         " chapter metadata for podcasts.")
        parser.add_argument("wav",
                            help="WAV file to convert/use")
        parser.add_argument("outdir",
                            help="directory in which to write output files. "
                                 "Will be created if nonexistant.")
        parser.add_argument("-c",
                            "--config",
                            help="configuration file to use",
                            default=os.path.expandvars(
                                "$HOME/.config/postshow.ini"))
        parser.add_argument("-m",
                            "--markers",
                            help="marker file to convert/use. Only Audacity "
                                 "labels are currently supported")
        parser.add_argument("-p",
                            "--profile",
                            default="default",
                            help="the configuration profile on which to base"
                                 "default values")
        args = parser.parse_args()
        errors = []
        if not os.path.exists(args.config):
            errors.append("Configuration file ({}) does not"
                          " exist".format(args.config))
        if not os.path.exists(args.wav):
            errors.append("Source WAV file ({}) does not exist".format(
                args.wav))
        if args.markers is not None and not os.path.exists(args.markers):
            errors.append("Markers file ({}) does not exist".format(
                args.markers))
        try:
            os.mkdir(args.outdir)
        except OSError as e:
            if e.errno != os.errno.EEXIST:
                errors.append(str(e))
        if len(errors) > 0:
            raise PostShowError(';\n'.join(errors))
        return args

    @staticmethod
    def check_config(path: str) -> configparser.ConfigParser:
        """Load the config file and check it for correctness."""
        config = configparser.ConfigParser()
        config.read(path)
        errors = []
        # Check every section of the config file, except for DEFAULT (which we
        # don't care about)
        for section in config:
            if section == "DEFAULT":
                continue
            so = config[section]
            # Just verify that the REQUIRED_TEXT_KEYS from above exist in the
            # file.  If they're just empty strings, that's the user's problem.
            for key in REQUIRED_TEXT_KEYS:
                if key not in so.keys():
                    errors.append('[{section}] is missing the required key'
                                  ' "{key}"'.format(section=section, key=key))
            # Verify that the REQUIRED_BOOK_KEYS from above exist in the file,
            # and are boolean values.
            for key in REQUIRED_BOOL_KEYS:
                if key not in so.keys():
                    errors.append('[{section}] is missing the required key'
                                  ' "{key}"'.format(section=section, key=key))
                else:
                    if so[key] not in ['True', 'False']:
                        errors.append('[{section}] must use Python boolean '
                                      'values ("True" or "False") for the key '
                                      '"{key}"'.format(section=section,
                                                       key=key))
        if len(errors) > 0:
            raise PostShowError(';\n'.join(errors))
        return config

    def do_encode(self):
        pass

    def main(self):
        """The primary logic of this program."""
        # Encoding
        self.metadata = self.ask_metadata()
        self.mp3_path = self.config.get(self.args.profile, 'filename').format(
            slug=self.config.get(self.args.profile, 'slug').lower(),
            epnum=self.metadata.number,
            ext='mp3'
        )
        self.m.setup(
            self.args.wav,
            os.path.join(
                self.args.outdir,
                self.mp3_path,
            ),
            self.config.get(self.args.profile, 'bitrate')
        )
        self.m.start()
        # Metadata conversion
        self.m.join()
        # Tagging
        t = MP3Tagger(self.mp3_path)


if __name__ == "__main__":
    m = Main()
    m.main()
