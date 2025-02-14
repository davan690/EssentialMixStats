import requests
from bs4 import BeautifulSoup
import re
import json
import time
from copy import deepcopy


DATE_REGEX = re.compile('(\d{4}-\d{2}-\d{2})')
VENUE_REGEX = re.compile(' @ (.*) - ')
ARTIST_REGEX = re.compile(' - (.*) [-|\(]')

LABEL_REGEX = re.compile('(\[.+\]$)')


# mixes that should be ignored, duplicates, not actually essential mixes etc
MIXES_TO_IGNORE = [
    '/w/1998-01_-_David_Holmes_-_Essential_Mix', '/w/2000_-_Fran%C3%A7ois_K_-_Essential_Mix'
]


def parse_mix_link(link_tag):
    """
    Parses the link, date, artist names and venues (if applicable) from the mixes db link text

    :return: the link and a dictionary with the rest of the data
    """
    mix_url = link_tag.attrs['href']

    if mix_url in MIXES_TO_IGNORE:
        return None, None
    title = link_tag.attrs['title']
    title = title.replace(' - Essential Mix', '').replace('(Essential Mix)', '')

    segments = title.split(' - ')

    if len(segments[0]) == 10:
        date = segments[0]
    else:
        # missing date for venue gig, need to handle differently
        match = DATE_REGEX.search(title)
        if match:
            date = match.groups()[0]
            segments[1] = segments[1].replace('(Essential Mix, %s)' % date, '')
        else:
            date = ''

    artist_venue = segments[1].split(' @ ')
    artists = [artist.strip() for artist in artist_venue[0].split(',')]
    venue = artist_venue[1].strip() if len(artist_venue) > 1 else ''

    return mix_url, {'date': date, 'artists': artists, 'venue': venue}


def get_next_page(page_soup):
    """
    Gets the url to the next page in the category if it exists
    """
    pagination = page_soup.find(class_='listPagination')
    for navigation in pagination.findAll('a'):
        if 'next' in navigation.get_text():
            return navigation.attrs['href']
    return None


def get_session():
    session = requests.Session()
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/74.0.3729.169 Safari/537.3'
    session.headers.update({
        'User-Agent': USER_AGENT,
    })
    return session

def get_tracklist_links(session):
    """
    Gets tracklist links and basic data for all the mixes in the Essential Mix category
    """
    try:
        # If a data file already exists, use that.
        with open('./data.json', 'r') as fp:
            data = json.load(fp)
    except Exception:
        next_page = True
        pages = []
        data = {}
        url = 'https://www.mixesdb.com/w/Category:Essential_Mix'

        while next_page:
            print(len(pages), url)
            response = session.get(url)
            soup = BeautifulSoup(response.content, 'html.parser')

            mixes = soup.find(id='catMixesList')
            mix_links = mixes.find_all('a')

            for link in mix_links:
                try:
                    mix_url, mix_data = parse_mix_link(link)
                    if mix_url and mix_data:
                        data[mix_url] = mix_data
                except Exception as e:
                    print(link)
                    print(e)

            next_page = get_next_page(soup)
            if next_page:
                pages.append(next_page)
                url = 'https://www.mixesdb.com/%s' % next_page
            time.sleep(5)

        with open('./data.json', 'w') as fp:
            json.dump(data, fp)
    return data


def get_tracklist_data(session, url):
    """
    Get a tracklist for an individual URL
    """
    try:
        # URLS with dots in them return an error when action=raw is appended use different url structures:
        if '.' in url:
            url = url.replace('/w/', '')
            response = session.get('https://www.mixesdb.com/db/index.php?title=%s&action=raw' % url)
        else:
            response = session.get('https://www.mixesdb.com%s?action=raw' % url)
        return response.content
    except Exception as e:
        print(url)
        print(e)


def get_tracklists(session, data):
    """
    Iterate all links in data and fetch individual tracklists for all entries that don't have them
    """
    for index, (url, mix_data) in enumerate(data.items()):
        if 'tracklist' not in mix_data:
            mix_data['tracklist'] = get_tracklist_data(session, url)
            time.sleep(2)

        if index % 100 == 0:
            print('Done %d/%d' % (index, len(data.items())))

    with open('./data.json', 'w') as fp:
        json.dump(data, fp)
    return data


def parse_tracklist(text):
    """
    Parse tracklist and categories from MixesDB detail page contents
    """
    tracklist, categories = None, None
    duplicate = False
    lines = text.split('\n')

    # duplicate or not fake mix:
    if any(message in lines[0] for message in ['Fake', 'Repeat']):
        duplicate = lines[1].replace(' |Original  =', '')
        return tracklist, categories, duplicate

    category_index = next((i for i, l in enumerate(lines) if '[[Category:' in l), len(lines))

    if '== Tracklist ==' in lines:
        tracklist = filter(None, lines[lines.index('== Tracklist ==') + 1: category_index])
        categories = filter(None, lines[category_index:])
        categories = [c.replace('[[Category:', '').replace(']]', '').strip() for c in categories]

    for category in categories:
        if '{{Repeated|' in category:
            duplicate = True

    return tracklist, categories, duplicate


def has_data(mix_data):
    try:
        tracks = 'tracks' in mix_data and mix_data['tracks'] and len(mix_data['tracks']) > 0
        categories = 'categories' in mix_data and mix_data['categories'] and len(mix_data['categories']) > 0
        duplicate =  'duplicate' in mix_data and mix_data['duplicate'] != False
        return (tracks and categories) or duplicate
    except Exception as e:
        print(mix_data)
        print(e)


def skip_track(track):
    skip_lines = ['<list>', '</list>']
    skip =  any(skip_line in track for skip_line in skip_lines)
    # ; is used to denote sections
    section = track[0] == ';'
    empty = track.strip() == ''
    return not(skip or section or empty)


def parse_tracks(mix_data):
    """
    Parse a list of raw track names into a dictionary of artist, track and label
    """
    parsed_tracks = []

    tracks = filter(skip_track, mix_data.get('tracks', []))

    for raw_track in tracks:
        processed_track = raw_track

        # remove leading '#' character
        if processed_track[0] == '#':
            processed_track = processed_track[1:].strip()

        # remove leading timestamps
        processed_track= re.sub('^\[[\d|\?|:]+\]', '', processed_track).strip()

        # remove leading numbers and periods
        processed_track = re.sub('^\d+\.', '', processed_track).strip()

        # remove leading superfluous characters
        for remove in ["''", "* ", "+ "]:
            if processed_track[0:2] == remove:
                processed_track = processed_track[2:].strip()

        artist, track, label = 'unknown', 'unknown', 'unknown'

        # Non identified tracks will often contain just question marks
        if not re.sub('\?+', '', processed_track).strip() == '':
            label_match = LABEL_REGEX.search(processed_track)
            label = label_match.group(0) if label_match else ''
            if label:
                processed_track = processed_track.replace(label, '').strip()
                label = label.replace('[', '').replace(']', '').strip()

            segments = processed_track.split(' - ')
            if len(segments) > 1:
                artist = segments[0].strip()
                # remove featuring listings from artists
                for feat in [' Feat.', ' Featuring']:
                    if feat in artist:
                        artist = artist.split(feat)[0]

                track = segments[1].strip()

        parsed_tracks.append([artist, track, label])

    fully_parsed = len(parsed_tracks) == len(tracks)

    if not fully_parsed:
        print(len(parsed_tracks), len(tracks))
        for index, track in enumerate(tracks):
            print(track)
            if len(parsed_tracks) > index:
                print(parsed_tracks[index])
            else:
                print('no track')

    return parsed_tracks


def parse_tracklists(data):
    """
    Parse tracklist data into tracklists and categories for all tracklists
    """
    for index, (url, mix_data) in enumerate(data.items()):

        # if data is not present parse it
        if not has_data(mix_data):
            tracks, categories, duplicate = parse_tracklist(mix_data['tracklist'])
            mix_data['categories'] = categories
            mix_data['tracks'] = tracks
            mix_data['duplicate'] = duplicate

        # data still not present, something is wrong:
        if not has_data(mix_data):
            print('https://www.mixesdb.com%s' % url)

        if not mix_data['duplicate']:
            mix_data['processed_tracks'] = parse_tracks(mix_data)

    with open('./data.json', 'w') as fp:
        json.dump(data, fp)
    return data


session = get_session()
data = get_tracklist_links(session)
data = get_tracklists(session, data)
data = parse_tracklists(data)

LENGTH_REGEX = re.compile('StandardShow(.*?)\}')

UNNECESSARY_CATEGORIES = [
    re.compile('Essential Mix\|\d\d\d\d-\d\d-\d\d'),
    re.compile('Ibiza \d\d\d\d'),
]

# venues that aren't properly parsed from titles
# todo should this be added to the venue attribute?
venue_categories = [
    "Cream",
    "Privilege (Ibiza)",
    "Unknown Gig / Location",
    "Space (Ibiza)",
    "Que Club",
    "Ministry Of Sound",
    "Glastonbury Festival",
    "One Big Weekend",
    "Creamfields",
    "Gatecrasher",
    "Homelands",
    "Surfcomber Hotel",
    "WMC",
    "The Warehouse Project",
    "Amnesia (Ibiza)",
    "Pacha (Ibiza)",
    "Sankeys (Ibiza)",
]

# artist names that aren't properly parsed from titles
artist_categories = [
    "Pete Tong",
    "Carl Cox",
    "Various",
    "John Digweed",
    "Danny Rampling",
    "Annie Mac",
    "Chemical Brothers",
    "Judge Jules",
    "Sasha",
    "Fergie",
    "Dave Pearce",
    "Fatboy Slim",
    "Paul Oakenfold",
    "Seb Fontaine",
    "Eddie Halliwell",
    "Eric Prydz"
]

artists = []
tracks = []
labels = []

def clean_data(data):
    """
    Remove data that is not needed for the front end piece and save the processed data to the data folder
    """
    processed_data = []
    for key, value in data.items():
        if not value['duplicate']:

            match = LENGTH_REGEX.search(value['tracklist'])
            if match:
                value['length'] = match.group(1)
            else:
                value['length'] = '?'

            # Clean up the categories
            remove_categories = list(set(['Essential Mix'] + value['artists'] + artist_categories + venue_categories))
            remove_categories.append(value['date'][0:4])

            categories = deepcopy(value['categories'])

            for category in value['categories']:
                if category.strip() in remove_categories:
                    categories.remove(category)
                for regex in UNNECESSARY_CATEGORIES:
                    if regex.match(category):
                        categories.remove(category)

            value['categories'] = categories
            value['tracklist'] = value['processed_tracks']
            del(value['processed_tracks'])
            del(value['tracks'])
            del(value['venue'])
            value['url'] = key

            processed_data.append(value)
    with open('./data/data.json', 'w') as fp:
        json.dump(processed_data, fp)

clean_data(data)