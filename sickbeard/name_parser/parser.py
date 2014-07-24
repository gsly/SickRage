# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of SickRage.
#
# SickRage is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SickRage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SickRage.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import with_statement

import os
import time
import re
import datetime
import os.path
import regexes
import sickbeard

from sickbeard import logger, helpers, scene_numbering, common, exceptions, scene_exceptions, encodingKludge as ek
from dateutil import parser


class NameParser(object):
    NORMAL_REGEX = 0
    SPORTS_REGEX = 1
    ANIME_REGEX = 2

    def __init__(self, file_name=True, showObj=None, tryIndexers=False, convert=False,
                 naming_pattern=False):

        self.file_name = file_name
        self.showObj = showObj
        self.tryIndexers = tryIndexers
        self.convert = convert
        self.naming_pattern = naming_pattern

        self.regexModes = [self.NORMAL_REGEX, self.SPORTS_REGEX, self.ANIME_REGEX]
        if self.showObj and not self.showObj.is_anime and not self.showObj.is_sports:
            self.regexModes = [self.NORMAL_REGEX]
        elif self.showObj and self.showObj.is_anime:
            self.regexModes = [self.ANIME_REGEX]
        elif self.showObj and self.showObj.is_sports:
            self.regexModes = [self.SPORTS_REGEX]

    def clean_series_name(self, series_name):
        """Cleans up series name by removing any . and _
        characters, along with any trailing hyphens.

        Is basically equivalent to replacing all _ and . with a
        space, but handles decimal numbers in string, for example:

        >>> cleanRegexedSeriesName("an.example.1.0.test")
        'an example 1.0 test'
        >>> cleanRegexedSeriesName("an_example_1.0_test")
        'an example 1.0 test'

        Stolen from dbr's tvnamer
        """

        series_name = re.sub("(\D)\.(?!\s)(\D)", "\\1 \\2", series_name)
        series_name = re.sub("(\d)\.(\d{4})", "\\1 \\2", series_name)  # if it ends in a year then don't keep the dot
        series_name = re.sub("(\D)\.(?!\s)", "\\1 ", series_name)
        series_name = re.sub("\.(?!\s)(\D)", " \\1", series_name)
        series_name = series_name.replace("_", " ")
        series_name = re.sub("-$", "", series_name)
        series_name = re.sub("^\[.*\]", "", series_name)
        return series_name.strip()

    def _compile_regexes(self, regexMode):
        if regexMode == self.SPORTS_REGEX:
            logger.log(u"Using SPORTS regexs", logger.DEBUG)
            uncompiled_regex = [regexes.sports_regexs]
        elif regexMode == self.ANIME_REGEX:
            logger.log(u"Using ANIME regexs", logger.DEBUG)
            uncompiled_regex = [regexes.anime_regexes, regexes.normal_regexes]
        else:
            logger.log(u"Using NORMAL reqgexs", logger.DEBUG)
            uncompiled_regex = [regexes.normal_regexes]

        self.compiled_regexes = []
        for regexItem in uncompiled_regex:
            for i, (cur_pattern_name, cur_pattern) in enumerate(regexItem):
                try:
                    cur_regex = re.compile(cur_pattern, re.VERBOSE | re.IGNORECASE)
                except re.error, errormsg:
                    logger.log(u"WARNING: Invalid episode_pattern, %s. %s" % (errormsg, cur_pattern))
                else:
                    cur_pattern_name = str(i) + "_" + cur_pattern_name
                    self.compiled_regexes.append((regexMode, cur_pattern_name, cur_regex))

    def _parse_string(self, name):
        if not name:
            return

        matches = []
        bestResult = None
        doneSearch = False

        for regexMode in self.regexModes:
            if doneSearch:
                break

            self._compile_regexes(regexMode)
            for (cur_regexMode, cur_regex_name, cur_regex) in self.compiled_regexes:
                match = cur_regex.match(name)

                if not match:
                    continue

                regex_num = int(re.match('^\d{1,2}', cur_regex_name).group(0))
                result = ParseResult(name)
                result.which_regex = [cur_regex_name]
                result.score = 0 - regex_num

                named_groups = match.groupdict().keys()

                if 'series_name' in named_groups:
                    result.series_name = match.group('series_name')
                    if result.series_name:
                        result.series_name = self.clean_series_name(result.series_name)
                        result.score += 1

                # get show object
                if not result.show and not self.naming_pattern:
                    result.show = helpers.get_show(result.series_name, self.tryIndexers)
                elif self.showObj and self.naming_pattern:
                    result.show = self.showObj

                # confirm result show object variables
                if result.show:
                    # confirm passed in show object indexer id matches result show object indexer id
                    if self.showObj and self.showObj.indexerid != result.show.indexerid:
                        doneSearch = True
                        break

                    # confirm we are using correct regex mode
                    if regexMode == self.NORMAL_REGEX and not (result.show.is_anime or result.show.is_sports):
                        result.score += 1
                    elif regexMode == self.SPORTS_REGEX and result.show.is_sports:
                        result.score += 1
                    elif regexMode == self.ANIME_REGEX and result.show.is_anime:
                        result.score += 1
                    elif not result.show.is_anime:
                        break

                if 'season_num' in named_groups:
                    tmp_season = int(match.group('season_num'))
                    if not (cur_regex_name == 'bare' and tmp_season in (19, 20)):
                        result.season_number = tmp_season
                        result.score += 1

                if 'ep_num' in named_groups:
                    ep_num = self._convert_number(match.group('ep_num'))
                    if 'extra_ep_num' in named_groups and match.group('extra_ep_num'):
                        result.episode_numbers = range(ep_num, self._convert_number(match.group('extra_ep_num')) + 1)
                        result.score += 1
                    else:
                        result.episode_numbers = [ep_num]
                        result.score += 1

                if 'ep_ab_num' in named_groups:
                    ep_ab_num = self._convert_number(match.group('ep_ab_num'))
                    if 'extra_ab_ep_num' in named_groups and match.group('extra_ab_ep_num'):
                        result.ab_episode_numbers = range(ep_ab_num,
                                                          self._convert_number(match.group('extra_ab_ep_num')) + 1)
                        result.score += 1
                    else:
                        result.ab_episode_numbers = [ep_ab_num]
                        result.score += 1

                if 'sports_event_id' in named_groups:
                    sports_event_id = match.group('sports_event_id')
                    if sports_event_id:
                        result.sports_event_id = int(match.group('sports_event_id'))
                        result.score += 1

                if 'sports_event_name' in named_groups:
                    result.sports_event_name = match.group('sports_event_name')
                    if result.sports_event_name:
                        result.sports_event_name = self.clean_series_name(result.sports_event_name)
                        result.score += 1

                if 'sports_air_date' in named_groups:
                    sports_air_date = match.group('sports_air_date')
                    if result.show and result.show.is_sports:
                        try:
                            result.sports_air_date = parser.parse(sports_air_date, fuzzy=True).date()
                            result.score += 1
                        except:
                            pass

                if 'air_year' in named_groups and 'air_month' in named_groups and 'air_day' in named_groups:
                    if result.show and result.show.air_by_date:
                        year = int(match.group('air_year'))
                        month = int(match.group('air_month'))
                        day = int(match.group('air_day'))

                        try:
                            dtStr = '%s-%s-%s' % (year, month, day)
                            result.air_date = datetime.datetime.strptime(dtStr, "%Y-%m-%d").date()
                            result.score += 1
                        except:
                            pass

                if 'extra_info' in named_groups:
                    tmp_extra_info = match.group('extra_info')

                    # Show.S04.Special or Show.S05.Part.2.Extras is almost certainly not every episode in the season
                    if not (tmp_extra_info and 'season_only' in cur_regex_name and re.search(
                            r'([. _-]|^)(special|extra)s?\w*([. _-]|$)', tmp_extra_info, re.I)):
                        result.extra_info = tmp_extra_info
                        result.score += 1

                if 'release_group' in named_groups:
                    result.release_group = match.group('release_group')
                    result.score += 1

                matches.append(result)

        if len(matches):
            # pick best match with highest score based on placement
            bestResult = max(sorted(matches, reverse=True, key=lambda x: x.which_regex), key=lambda x: x.score)

            # get quality
            bestResult.quality = common.Quality.nameQuality(name,
                                                            bestResult.show.is_anime if bestResult.show else False)

            # if this is a naming pattern test or result doesn't have a show object then return best result
            if not bestResult.show or bestResult.is_air_by_date or bestResult.is_sports or self.naming_pattern:
                return bestResult

            new_episode_numbers = []
            new_season_numbers = []
            new_absolute_numbers = []

            if bestResult.show.is_anime and len(bestResult.ab_episode_numbers):
                scene_season = scene_exceptions.get_scene_exception_by_name(bestResult.series_name)[1]
                for epAbsNo in bestResult.ab_episode_numbers:
                    a = epAbsNo

                    if self.convert:
                        a = scene_numbering.get_indexer_absolute_numbering(bestResult.show.indexerid,
                                                                           bestResult.show.indexer, epAbsNo,
                                                                           True, scene_season)
                    try:
                        (s, e) = helpers.get_all_episodes_from_absolute_number(bestResult.show, [a])
                    except exceptions.EpisodeNotFoundByAbsoluteNumberException:
                        logger.log(str(bestResult.show.indexerid) + ": Indexer object absolute number " + str(
                            epAbsNo) + " is incomplete, skipping this episode")
                        return bestResult
                    else:
                        new_absolute_numbers.append(a)
                        new_episode_numbers.extend(e)
                        new_season_numbers.append(s)

            elif bestResult.season_number and len(bestResult.episode_numbers):
                for epNo in bestResult.episode_numbers:
                    s = bestResult.season_number
                    e = epNo

                    if self.convert:
                        (s, e) = scene_numbering.get_indexer_numbering(bestResult.show.indexerid,
                                                                       bestResult.show.indexer,
                                                                       bestResult.season_number,
                                                                       epNo)
                    if bestResult.show.is_anime:
                        a = helpers.get_absolute_number_from_season_and_episode(bestResult.show, s, e)
                        if a:
                            new_absolute_numbers.append(a)

                    new_episode_numbers.append(e)
                    new_season_numbers.append(s)

            # need to do a quick sanity check heregex.  It's possible that we now have episodes
            # from more than one season (by tvdb numbering), and this is just too much
            # for sickbeard, so we'd need to flag it.
            new_season_numbers = list(set(new_season_numbers))  # remove duplicates
            if len(new_season_numbers) > 1:
                raise InvalidNameException("Scene numbering results episodes from "
                                           "seasons %s, (i.e. more than one) and "
                                           "sickrage does not support this.  "
                                           "Sorry." % (str(new_season_numbers)))

            # I guess it's possible that we'd have duplicate episodes too, so lets
            # eliminate them
            new_episode_numbers = list(set(new_episode_numbers))
            new_episode_numbers.sort()

            # maybe even duplicate absolute numbers so why not do them as well
            new_absolute_numbers = list(set(new_absolute_numbers))
            new_absolute_numbers.sort()

            if len(new_absolute_numbers):
                bestResult.ab_episode_numbers = new_absolute_numbers

            if len(new_season_numbers) and len(new_episode_numbers):
                bestResult.episode_numbers = new_episode_numbers
                bestResult.season_number = new_season_numbers[0]

            if self.convert:
                logger.log(
                    u"Converted parsed result " + bestResult.original_name + " into " + str(bestResult).decode('utf-8',
                                                                                                               'xmlcharrefreplace'),
                    logger.DEBUG)

        # CPU sleep
        time.sleep(0.02)

        return bestResult

    def _combine_results(self, first, second, attr):
        # if the first doesn't exist then return the second or nothing
        if not first:
            if not second:
                return None
            else:
                return getattr(second, attr)

        # if the second doesn't exist then return the first
        if not second:
            return getattr(first, attr)

        a = getattr(first, attr)
        b = getattr(second, attr)

        # if a is good use it
        if a != None or (type(a) == list and len(a)):
            return a
        # if not use b (if b isn't set it'll just be default)
        else:
            return b

    def _unicodify(self, obj, encoding="utf-8"):
        if isinstance(obj, basestring):
            if not isinstance(obj, unicode):
                obj = unicode(obj, encoding, 'replace')
        return obj

    def _convert_number(self, org_number):
        """
         Convert org_number into an integer
         org_number: integer or representation of a number: string or unicode
         Try force converting to int first, on error try converting from Roman numerals
         returns integer or 0
         """

        try:
            # try forcing to int
            if org_number:
                number = int(org_number)
            else:
                number = 0

        except:
            # on error try converting from Roman numerals
            roman_to_int_map = (('M', 1000), ('CM', 900), ('D', 500), ('CD', 400), ('C', 100),
                                ('XC', 90), ('L', 50), ('XL', 40), ('X', 10),
                                ('IX', 9), ('V', 5), ('IV', 4), ('I', 1)
            )

            roman_numeral = str(org_number).upper()
            number = 0
            index = 0

            for numeral, integer in roman_to_int_map:
                while roman_numeral[index:index + len(numeral)] == numeral:
                    number += integer
                    index += len(numeral)

        return number

    def parse(self, name, cache_result=True):
        name = self._unicodify(name)

        if self.naming_pattern:
            cache_result = False

        cached = name_parser_cache.get(name)
        if cached:
            return cached

        # break it into parts if there are any (dirname, file name, extension)
        dir_name, file_name = ek.ek(os.path.split, name)

        if self.file_name:
            base_file_name = helpers.remove_extension(file_name)
        else:
            base_file_name = file_name

        # set up a result to use
        final_result = ParseResult(name)

        # try parsing the file name
        file_name_result = self._parse_string(base_file_name)

        # use only the direct parent dir
        dir_name = os.path.basename(dir_name)

        # parse the dirname for extra info if needed
        dir_name_result = self._parse_string(dir_name)

        # build the ParseResult object
        final_result.air_date = self._combine_results(file_name_result, dir_name_result, 'air_date')

        # anime absolute numbers
        final_result.ab_episode_numbers = self._combine_results(file_name_result, dir_name_result, 'ab_episode_numbers')

        # sports
        final_result.sports_event_id = self._combine_results(file_name_result, dir_name_result, 'sports_event_id')
        final_result.sports_event_name = self._combine_results(file_name_result, dir_name_result, 'sports_event_name')
        final_result.sports_air_date = self._combine_results(file_name_result, dir_name_result, 'sports_air_date')

        if not final_result.air_date and not final_result.sports_air_date:
            final_result.season_number = self._combine_results(file_name_result, dir_name_result, 'season_number')
            final_result.episode_numbers = self._combine_results(file_name_result, dir_name_result, 'episode_numbers')

        # if the dirname has a release group/show name I believe it over the filename
        final_result.series_name = self._combine_results(dir_name_result, file_name_result, 'series_name')
        final_result.extra_info = self._combine_results(dir_name_result, file_name_result, 'extra_info')
        final_result.release_group = self._combine_results(dir_name_result, file_name_result, 'release_group')

        final_result.which_regex = []
        if final_result == file_name_result:
            final_result.which_regex = file_name_result.which_regex
        elif final_result == dir_name_result:
            final_result.which_regex = dir_name_result.which_regex
        else:
            if file_name_result:
                final_result.which_regex += file_name_result.which_regex
            if dir_name_result:
                final_result.which_regex += dir_name_result.which_regex

        final_result.show = self._combine_results(file_name_result, dir_name_result, 'show')
        final_result.quality = self._combine_results(file_name_result, dir_name_result, 'quality')

        if not final_result.show:
            raise InvalidShowException(
                "Unable to parse " + name.encode(sickbeard.SYS_ENCODING, 'xmlcharrefreplace'))

        # if there's no useful info in it then raise an exception
        if final_result.season_number == None and not final_result.episode_numbers and final_result.air_date == None and final_result.sports_air_date == None and not final_result.ab_episode_numbers and not final_result.series_name:
            raise InvalidNameException("Unable to parse " + name.encode(sickbeard.SYS_ENCODING, 'xmlcharrefreplace'))

        if cache_result:
            name_parser_cache.add(name, final_result)

        logger.log(u"Parsed " + name + " into " + str(final_result).decode('utf-8', 'xmlcharrefreplace'), logger.DEBUG)
        return final_result


class ParseResult(object):
    def __init__(self,
                 original_name,
                 series_name=None,
                 sports_event_id=None,
                 sports_event_name=None,
                 sports_air_date=None,
                 season_number=None,
                 episode_numbers=None,
                 extra_info=None,
                 release_group=None,
                 air_date=None,
                 ab_episode_numbers=None,
                 show=None,
                 score=None,
                 quality=None
    ):

        self.original_name = original_name

        self.series_name = series_name
        self.season_number = season_number
        if not episode_numbers:
            self.episode_numbers = []
        else:
            self.episode_numbers = episode_numbers

        if not ab_episode_numbers:
            self.ab_episode_numbers = []
        else:
            self.ab_episode_numbers = ab_episode_numbers

        if not quality:
            self.quality = common.Quality.UNKNOWN
        else:
            self.quality = quality

        self.extra_info = extra_info
        self.release_group = release_group

        self.air_date = air_date

        self.sports_event_id = sports_event_id
        self.sports_event_name = sports_event_name
        self.sports_air_date = sports_air_date

        self.which_regex = []
        self.show = show
        self.score = score

    def __eq__(self, other):
        if not other:
            return False

        if self.series_name != other.series_name:
            return False
        if self.season_number != other.season_number:
            return False
        if self.episode_numbers != other.episode_numbers:
            return False
        if self.extra_info != other.extra_info:
            return False
        if self.release_group != other.release_group:
            return False
        if self.air_date != other.air_date:
            return False
        if self.sports_event_id != other.sports_event_id:
            return False
        if self.sports_event_name != other.sports_event_name:
            return False
        if self.sports_air_date != other.sports_air_date:
            return False
        if self.ab_episode_numbers != other.ab_episode_numbers:
            return False
        if self.show != other.show:
            return False
        if self.score != other.score:
            return False
        if self.quality != other.quality:
            return False

        return True

    def __str__(self):
        if self.series_name != None:
            to_return = self.series_name + u' - '
        else:
            to_return = u''
        if self.season_number != None:
            to_return += 'S' + str(self.season_number)
        if self.episode_numbers and len(self.episode_numbers):
            for e in self.episode_numbers:
                to_return += 'E' + str(e)

        if self.is_air_by_date:
            to_return += str(self.air_date)
        if self.is_sports:
            to_return += str(self.sports_event_name)
            to_return += str(self.sports_event_id)
            to_return += str(self.sports_air_date)
        if self.ab_episode_numbers:
            to_return += ' [Absolute Nums: ' + str(self.ab_episode_numbers) + ']'
        if self.release_group:
            to_return += ' [GROUP: ' + self.release_group + ']'

        to_return += ' [ABD: ' + str(self.is_air_by_date) + ']'
        to_return += ' [SPORTS: ' + str(self.is_sports) + ']'
        to_return += ' [ANIME: ' + str(self.is_anime) + ']'
        to_return += ' [whichReg: ' + str(self.which_regex) + ']'

        return to_return.encode('utf-8')

    @property
    def is_air_by_date(self):
        if self.season_number == None and len(self.episode_numbers) == 0 and self.air_date:
            return True
        return False

    @property
    def is_sports(self):
        if self.season_number == None and len(self.episode_numbers) == 0 and self.sports_air_date:
            return True
        return False

    @property
    def is_anime(self):
        if len(self.ab_episode_numbers):
            return True
        return False


class NameParserCache(object):
    _previous_parsed = {}
    _cache_size = 100

    def add(self, name, parse_result):
        self._previous_parsed[name] = parse_result
        _current_cache_size = len(self._previous_parsed)
        if _current_cache_size > self._cache_size:
            for i in range(_current_cache_size - self._cache_size):
                del self._previous_parsed[self._previous_parsed.keys()[0]]

    def get(self, name):
        if name in self._previous_parsed:
            logger.log("Using cached parse result for: " + name, logger.DEBUG)
            return self._previous_parsed[name]


name_parser_cache = NameParserCache()


class InvalidNameException(Exception):
    "The given release name is not valid"


class InvalidShowException(Exception):
    "The given show name is not valid"