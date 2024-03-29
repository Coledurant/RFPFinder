from bs4 import BeautifulSoup
import requests
import warnings
import urllib.request
from contextlib import closing
import shutil
import re
from collections import Counter
import os
import json
import sys
from datetime import datetime, timedelta
import logging
warnings.filterwarnings("ignore", category=UserWarning, module='bs4')

logger = logging.getLogger(__name__)

curr_dir = os.getcwd()
data_dir = os.path.join(curr_dir, 'data')
fbo_dir = os.path.join(data_dir, 'FBO')
out_path = os.path.join(fbo_dir,"nightly_files")






def clean_line_text(line_text):
    '''
    Given a line of text from an FBO FTP file, clean it up using bs4
    Parameters:
        line_text (str): a line of text from the ftp file
    Returns:
        text (str): the sanitized text
    '''
    url_regex = re.compile(
            r'^(?:http|ftp)s?://' # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|' #domain...
            r'localhost|' #localhost...
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})' # ...or ip
            r'(?::\d+)?' # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    m = re.match(url_regex, line_text)
    if m:
        #bs4 raises warnings when you try to parse a url
        return line_text
    soup = BeautifulSoup(line_text,'html.parser')
    try:
        href = soup.find('a',href=True)['href']
    except TypeError:
        href = None
    #windows-1252 is more expansive than latin1
    soup_text = f'{href} {soup.text}' if href else soup.text
    text = soup_text.encode('windows-1252', errors = 'ignore').decode("utf8", errors='ignore')
    text = text.replace('Link To Document','').strip()

    return text

def get_email_from_url(url):
    '''
    Given the url to an fbo page, extract the contact email
    Parameters:
        url (str): the url to an fbo page
    Returns:
        hrefs (list): a list of all of the hrefs scraped from the page
    '''
    try:
        r = requests.get(url, timeout=20)
    except:
        return
    content = r.content
    soup = BeautifulSoup(content, 'html.parser')
    hrefs = []
    for link in soup.findAll('a', attrs={'href': re.compile("^mailto")}):
        hrefs.append(link.get('href'))

    return hrefs

def extract_emails(notice):
    '''
    Given a contact field from a notice, extract the email addresses and first contact name.
    Parameters:
        notice (dict): a dict representing a single fbo notice from their FTP

    Returns:
        emails (list): a list of unique email addresses
    '''
    email_re = re.compile(r'^([0-9a-zA-Z]([-.\w]*[0-9a-zA-Z])*@([0-9a-zA-Z][-\w]*[0-9a-zA-Z]\.)+[a-zA-Z]{2,9})$')
    emails = []
    #search the contact field first
    contact = notice.get('CONTACT')
    if contact:
        tokens = contact.split()
        for token in tokens:
            m = re.search(email_re, token)
            if m:
                emails.append(m.group())
    #If there's no email address, the notice might have an email field, even though
    #the FBO docs say this field isn't to be used.
    try:
        #pop since we don't need this key in each notice anymore.
        email = notice.pop('EMAIL')
    except KeyError:
        email = None
    if not emails and email:
        tokens = email.split()
        for token in tokens:
            m = re.search(email_re, token)
            if m:
                emails.append(m.group())
    #if there's still no email in the contact field, move onto all of the other fields
    if not emails:
        notice_values = " ".join(notice.values())
        tokens = notice_values.split()
        for token in tokens:
            m = re.search(email_re, token)
            if m:
                emails.append(m.group())
    #if there's still no email address, try web-scraping the notice's fbo page
    if not emails:
        url = notice.get('URL')
        hrefs = get_email_from_url(url)
        hrefs = [x.replace("mailto:",'') for x in hrefs]
        if hrefs:
            matches = [re.search(email_re, href.strip()) for href in hrefs]
            emails = [m.group() for m in matches if m is not None]
    emails = [email.lower() for email in set(emails)] if emails else None

    return emails

def get_redirect_url(h):
    '''
    Given a HEAD response, return the redirect location.

    Parameters:
        h (a requests response)

    Returns:
        redirect_url (str): the location field from the response (h) headers.
    '''
    location = h.headers.get('Location')
    if 'www.fbo.gov' not in location:
        redirect_url = f'https://www.fbo.gov{location}'
    else:
        redirect_url = location

    return redirect_url

def handle_archive_redirect(url, redirect_url, cookies, notice_date, notice_type):
    '''
    Given a redirect url, scrape the notice table from it to find the correct notice url
    given the other params.

    Parameters:
        url (str): the notice's url from the FTP file
        redirect_url (str): the location value from the HEAD request response headers
        cookies (requests CookieJar object): the cookies from the HEAD request
        notice_date (str): the DATE and YEAR fields of the notice concatenated together
        notice_type (str): the notice's type (one of ['PRESOL','COMBINE','MOD','AMDCSS'])

    Returns:
        notice_url (str): the notice's url
    '''
    try:
        r = requests.get(redirect_url, cookies = cookies)
    except Exception as err:
        logger.error(f"Exception in handle_archive_redirect making GET request to {url}: \
                     {err}", exc_info=True)
        return
    soup = BeautifulSoup(r.content, 'html.parser')
    try:
        archive_list = soup.find('table', {'class':'list'}).find_all('tr')
    except AttributeError as err:
        logger.error(f"Exception occurred finding the archive list from the redirect given by {url}: \
                     {err}", exc_info=True)
        return
    notice_url = get_notice_url_from_archive_list(redirect_url, archive_list, notice_date, notice_type)

    return notice_url


def get_notice_url_from_archive_list(redirect_url, archive_list, notice_date, notice_type):
    '''
    Given the html table from the redirect, find the correct notice url

    Parameters:
        redirect_url (str): the location value from the HEAD request response headers
        archive_list (bs4 soup object): an html table
        notice_date (str): the DATE and YEAR fields of the notice concatenated together
        notice_type (str): the notice's type (one of ['PRESOL','COMBINE','MOD','AMDCSS'])

    Returns:
        notice_url (str or None): the notice's url. If None, it's because there were not matching notices
        in the table. This sometimes happens, likely for older notices. These will be replaced with the
        original notice url.
    '''
    notice_type_map = {'PRESOL':'Presolicitation',
                       'AMDCSS':'Combined Synopsis/Solicitation (Modified)',
                       'COMBINE':'Combined Synopsis/Solicitation',
                       'MOD':'(Modified)'}
    sol_type_to_find = notice_type_map[notice_type]
    notice_date = datetime.strptime(notice_date, "%m%d%y")
    notice_url = None
    for i, row in enumerate(archive_list):
        try:
            posted_on_date_str = row.find('td',{'class':'lst-cl',
                                          'headers':'lh_current_posted_date'}).get_text().strip()
        except AttributeError:
            continue
        posted_on_date = datetime.strptime(posted_on_date_str, "%b %d, %Y")
        try:
            sol_type = row.find('td',{'class':'lst-cl',
                                'headers':'lh_base_type'}).get_text().strip()
        except AttributeError:
            continue
        if posted_on_date == notice_date:
            if sol_type_to_find in sol_type:
                if sol_type_to_find == '(Modified)':
                    if '(Modified)' in sol_type:
                        notice_url = archive_list[i].find('td',
                                                          {'class':'lst-cl',
                                                           'headers':'lh_id'}).find('a',href=True)['href']
                elif sol_type_to_find == 'Combined Synopsis/Solicitation':
                    if '(Modified)' not in sol_type:
                        notice_url = archive_list[i].find('td',
                                                          {'class':'lst-cl',
                                                           'headers':'lh_id'}).find('a',href=True)['href']
                    else:
                        continue
                else:
                    notice_url = archive_list[i].find('td',
                                                          {'class':'lst-cl',
                                                           'headers':'lh_id'}).find('a',href=True)['href']
                if notice_url:
                    if 'https://www.fbo.gov/index' not in notice_url:
                        notice_url = f'https://www.fbo.gov/index{notice_url}'
                    return notice_url
        else:
            continue

    return notice_url

def handle_dla_url(url, notice_date, notice_type):
    '''
    Given the url from a notice, check if it's a DLA url. If so, get the actual notice url. If not,
    return the url as-is. DLA = Defense Logistics Agency

    Parameters:
        url (str): the notice's url extracted from the FTP file
        notice_date (str): the DATE and YEAR fields of the notice concatenated together
        notice_type (str): the notice's type (one of ['PRESOL','COMBINE','MOD','AMDCSS'])

    Returns:
        notice_url (str): the notice's url
    '''

    if 'spg/dla' not in url.lower():
        notice_url = url
        return notice_url
    try:
        h = requests.head(url)
    except Exception as err:
        logger.error(f"Exception occurred making HEAD request to {url}:  \
                     {err}", exc_info=True)
        return
    status_code = h.status_code
    if status_code == 302:
        redirect_url = get_redirect_url(h)
    elif status_code == 200:
        return url
    else:
        logger.error(f"Non-200/302 status code of {status_code} making HEAD request to {url}")
        return
    if 'archive' in redirect_url or redirect_url == 'https://www.fbo.gov/index?s=opportunity&mode=list&tab=list':
        cookies = h.cookies
        notice_url = handle_archive_redirect(url, redirect_url, cookies, notice_date, notice_type)
    else:
        notice_url = redirect_url
    if notice_url:
        notice_url = re.sub(r' +','',notice_url)
        return notice_url
    else:
        return url


def id_and_count_notice_tags(file_lines):
    '''
    Static method to count the number of notice tags within an FBO export.
    Attributes:
        file_lines (list): A list of lines from the nightly FBO file.
    Returns:
        tag_count (dict): An instance of a collections.Counter object
                           containing tags as keys and their counts as
                           values
    '''
    end_tag = re.compile(r'\</[A-Z]*>')
    alphas_re = re.compile('[^a-zA-Z]')
    tags = []   # instantiate empty list
    for line in file_lines:
        try:
            match = end_tag.search(line)
            m = match.group()
            tags.append(m)
        except AttributeError:
            # these are all of the non record-type tags
            pass
    clean_tags = [alphas_re.sub('', x) for x in tags]
    tag_count = Counter(clean_tags)

    return tag_count


def merge_dicts(dicts):
    '''
    Given a list of dictionaries, merge them into one dictionary, joining the str values
    where keys overlap.
    '''
    d = {}
    for dict in dicts:
        for key in dict:
            try:
                d[key].append(dict[key])
            except KeyError:
                d[key] = [dict[key]]
    return {k:" ".join(v) for k, v in d.items()}


def make_out_path(out_path):
    '''
    makes a directory in the current working directory if it doesn't already exist.
    '''
    if not os.path.exists(out_path):
        os.makedirs(out_path)


def download_from_ftp(date, fbo_ftp_url):
    '''
    Downloads a nightly FBO file, reads the lines, then removes file.
    Compare to read_from_ftp()

    Parameters:
        date (str): the date of the FTP file being downloaded
        fbo_ftp_url (str): the FBO FTP url
    Returns:
        file_lines (list): the lines of the nightly file
    '''
    file_name = f'fbo_nightly_{date}'
    make_out_path(out_path)
    try:
        with closing(urllib.request.urlopen(fbo_ftp_url, timeout=20)) as r:
            file_name = os.path.join(out_path,file_name)
            with open(file_name, 'wb') as f:
                shutil.copyfileobj(r, f)
    except Exception as err:
        logger.critical(f"Exception occurred trying to access {fbo_ftp_url}:  \
                          {err}", exc_info=True)
        return
    with open(file_name,'r', errors='ignore') as f:
        file_lines = f.readlines()
    os.remove(file_name)

    return file_lines


def pseudo_xml_to_json(file_lines):
    '''
    Open a nightly file and convert the pseudo-xml to a JSON compatible dictionary
    Arguments:
        file_name (str): the absolute path to the downloaded file
    Returns:
        merge_notices_dict (dict): a dictionary with keys for each notice type and arrays of notice
                                   dicts as values.
    '''
    html_tags = ['a', 'abbr', 'acronym', 'address', 'applet', 'area', 'article', 'aside', 'audio', 'b', 'base', 'basefont',
                 'bdi', 'bdo', 'bgsound', 'big', 'blink', 'blockquote', 'body', 'br', 'button', 'canvas', 'caption', 'center',
                 'cite', 'code', 'col', 'colgroup', 'command', 'content', 'data', 'datalist', 'dd', 'del', 'details', 'dfn',
                 'dialog', 'dir', 'div', 'dl', 'dt', 'element', 'em', 'embed', 'fieldset', 'figcaption', 'figure', 'font',
                 'footer', 'form', 'frame', 'frameset', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'head', 'header', 'hgroup', 'hr',
                 'html', 'i', 'iframe', 'image', 'img', 'input', 'ins', 'isindex', 'kbd', 'keygen', 'label', 'legend', 'li',
                 'link', 'listing', 'main', 'map', 'mark', 'marquee', 'math', 'menu', 'menuitem', 'meta', 'meter', 'multicol',
                 'nav', 'nextid', 'nobr', 'noembed', 'noframes', 'noscript', 'object', 'ol', 'optgroup', 'option', 'output',
                 'p', 'param', 'picture', 'plaintext', 'pre', 'progress', 'q', 'rb', 'rbc', 'rp', 'rt', 'rtc', 'ruby', 's',
                 'samp', 'script', 'section', 'select', 'shadow', 'slot', 'small', 'source', 'spacer', 'span', 'strike',
                 'strong', 'style', 'sub', 'summary', 'sup', 'svg', 'table', 'tbody', 'td', 'template', 'textarea', 'tfoot',
                 'th', 'thead', 'time', 'title', 'tr', 'track', 'tt', 'u', 'ul', 'var', 'video', 'wbr', 'xmp']
    html_tag_re = re.compile(r'|'.join('(?:</?{0}>)'.format(x) for x in html_tags), flags = re.I)
    alphas_re = re.compile('[^a-zA-Z]')
    notice_types = {'PRESOL','SRCSGT','SNOTE','SSALE','COMBINE','AMDCSS',
                    'MOD','AWARD','JA','FAIROPP','ARCHIVE','UNARCHIVE',
                    'ITB','FSTD','EPSUPLOAD','DELETE'}
    notice_type_start_tag_re = re.compile(r'|'.join('(?:<{0}>)'.\
                                          format(x) for x in notice_types))
    notice_type_end_tag_re = re.compile(r'|'.join('(?:</{0}>)'.\
                                        format(x) for x in notice_types))
    # returns two groups: the sub-tag as well as the text corresponding to it
    sub_tag_groups = re.compile(r'\<([A-Z]*)\>(.*)')
    notices_dict_incrementer = {k:0 for k in notice_types}
    tag_count = id_and_count_notice_tags(file_lines)
    matches_dict = {k:{k:[] for k in range(v)} for k,v in tag_count.items()}
    # Loop through each line searching for start-tags, then end-tags, then
    # sub-tags (after stripping html) and then ensuring that every line of
    # multi-line tag values is captured.
    last_clean_notice_start_tag = ''
    last_sub_tab = ''
    for line in file_lines:
        line = line.replace("<br />",' ')
        try:
            match = notice_type_start_tag_re.search(line)
            m = match.group()
            clean_notice_start_tag = alphas_re.sub('', m)
            last_clean_notice_start_tag = clean_notice_start_tag
        except AttributeError:
            try:
                match = notice_type_end_tag_re.search(line)
                m = match.group()
                notices_dict_incrementer[last_clean_notice_start_tag] += 1
                continue #continue since we found an ending notice tag
            except AttributeError:
                line_htmless = ' '.join(html_tag_re.sub(' ',
                                                        line.replace(u'\xa0', u' ')).split())
                try:
                    matches = sub_tag_groups.search(line_htmless)
                    groups  = matches.groups()
                    sub_tag = groups[0]
                    last_sub_tab = sub_tag
                    sub_tag_text = clean_line_text(groups[1])
                    current_tag_index = notices_dict_incrementer[last_clean_notice_start_tag]
                    matches_dict[last_clean_notice_start_tag][current_tag_index].append({sub_tag:sub_tag_text})
                except AttributeError:
                    record_index = 0
                    for i, record in enumerate(matches_dict[last_clean_notice_start_tag][current_tag_index]):
                        if last_sub_tab in record:
                            record_index = i
                    matches_dict[last_clean_notice_start_tag][current_tag_index][record_index][last_sub_tab] += " " + clean_line_text(line_htmless)
    notices_dict = {k:None for k in notice_types}
    for k in matches_dict:
        dict_list = [v for k,v in matches_dict[k].items()]
        notices_dict[k] = dict_list

    merge_notices_dict = {k:[] for k in notices_dict}
    for k in notices_dict:
        notices = notices_dict[k]
        if notices:
            for notice in notices:
                merged_dict = merge_dicts(notice)
                merge_notices_dict[k].append(merged_dict)
        else:
            pass
    merge_notices_dict

    return merge_notices_dict


def filter_json(merge_notices_dict, notice_types, naics):
    '''
    Filter out undesireable notice types and NAICS codes from merge_notices_dict. Then
    lowercase all of the keys in the notice dictionaries.
    Parameters:
        merge_notices_dict (dict): a dictionary with keys for each notice type and arrays of notice
                                   dicts as values.
        notice_types (list): a list of notice_types to keep
        naics (list): naics (list): NAICS numbers to include.
    Returns:
        filtered_data (dict): a dictionary with keys for desired notice type and arrays of notice
                              dicts that match the NACIS as values.
    '''
    filtered_data = {k:[] for k in notice_types}
    for notice_type in merge_notices_dict:
        if notice_type not in notice_types:
            continue
        notices = merge_notices_dict[notice_type]
        for notice in notices:
            try:
                notice_naics = notice['NAICS']
            except KeyError:
                #if there's no NAICS, can't properly filter it
                continue
            # see if the NAICS starts with any of the naics codes provided by self
            if any(notice_naics.startswith(n) for n in naics):
                try:
                    notice_url = notice_url = re.sub(r' +','', notice['URL'])
                    n_date = notice['DATE']
                    n_year = notice['YEAR']
                    notice_date = n_date+n_year
                except KeyError:
                    continue
                correct_notice_url = handle_dla_url(notice_url, notice_date, notice_type)
                notice['URL'] = correct_notice_url
                notice['EMAILS'] = extract_emails(notice)
                notice = {k.lower():v for k,v in notice.items()}
                stripped_notice = {k:None for k in notice}
                for k in notice:
                    v = notice[k]
                    if isinstance(v, str):
                        v = v.strip()
                        stripped_notice[k] = v
                    else:
                        stripped_notice[k] = v
                filtered_data[notice_type].append(stripped_notice)

    return filtered_data



def get_nightly_data(date = None,
                     notice_types = ['MOD','PRESOL','COMBINE', 'AMDCSS'],
                     naics = ['2211','221210', '541330', '881412']):
    '''
    Exectutes methods in fbo_nightly_scraper module.
    Parameters:
        date (None or str): if a str, must be a date of th "%Y%m%d" format. If none, defaults to
                            (datetime.now() - timedelta(2)).strftime("%Y%m%d")
        notice_types (list): notice types to scrape from fbo.
        naics (list): NAICS numbers to include. Default value is energy/utility related NAICS.
    Returns:
        nightly_data (list): list of dicts in JSON format.
    '''

    if not date:
        #get day before yesterday to give FBO time to update their FTP
        now_minus_two = datetime.utcnow() - timedelta(2)
        date = now_minus_two.strftime("%Y%m%d")
    fbo_ftp_url = f'ftp://ftp.fbo.gov/FBOFeed{date}'
    file_lines = download_from_ftp(date, fbo_ftp_url)
    if not file_lines:
        #exit program if download_from_ftp() failed (this is logged by the module)
        sys.exit(1)
    merge_notices_dict = pseudo_xml_to_json(file_lines)
    nightly_data = filter_json(merge_notices_dict, notice_types, naics)

    return nightly_data


def check_desc(desc_str, phrases):

    '''
    Checks if any of the required phrases (phrases) are in the rfp description (desc_str)
    Not case sensitive
    Parameters:
        desc_str (str): rfp description
        phrases (list): List of phrases to check for
    Returns:
        Boolean depending on if any of the phrases are in the description
    '''

    if any(phrase.lower() in desc_str.lower() for phrase in phrases):
        return True
    else:
        return False


def check_agency(agency_str, agencies):

    '''
    Checks if the rfp agency (agency_str) is in the list of required agencies (agencies)
    Parameters:
        agency_str (str): Agency string of the rfp
        agencies (list): List of agencies to check for
    Returns:
        Boolean depending on if the agency_str is in the list of agencies
    '''

    if agency_str.lower() in [agency.lower() for agency in agencies]:

        return True

    else:

        return False


def get_message_field(data_dict, phrases = ['battery storage', 'microgrids', 'minigrids', 'demand response',
                        'energy efficiency', 'multi-unit housing', 'Non-Wires Alternative', 'NWA',
                        'transformer', 'substation', 'circuit', 'Smart cities', 'frequency'],
                        agencies = ['Department of Energy'],
                        check_for_phrases = True,
                        check_for_agency = True):

    '''
    Puts together email message field
    Parameters:
        data_dict (dict): Nightly data dict of rfps
        phrases (list): List of phrases to check the rfp description for if check_for_phrases is True
        agencies (list): List of rfp agencies to check for if check_for_agency is True
        check_for_phrases (boolean): Boolean for whether or not you want only rfps with the phrases in their description
        check_for_agency (boolean): Boolean for whether or not you want only rfps from pre-specified agencies
    Returns:
        message_field (str):
    '''

    rfp_strings = []

    for notice_type, datalist in data_dict.items():

        for rfp in datalist:

            if isinstance(rfp['desc'], (list,)):
                desc_str = ' '.join(rfp['desc'])
            elif isinstance(rfp['desc'], (str,)):
                desc_str = rfp['desc']
            else:
                desc_str = 'No description found!'

            if isinstance(rfp['emails'], (list,)):
                emails_str = ' '.join(rfp['emails'])
            elif isinstance(rfp['emails'], (str,)):
                emails_str = rfp['emails']
            else:
                emails_str = 'No emails found!'

            try:
                ntype = '\n' + 'Notice Type: ' + rfp['ntype']
            except KeyError:
                ntype = ''

            rfp_str = 'Agency: ' + rfp['agency'] + '\n' + \
            'NAICS Code: ' + rfp['naics'] + ntype + '\n' + \
            'Date: ' + '{}/{}/{}'.format(rfp['date'][:2], rfp['date'][2:], rfp['year']) + '\n' + \
            'Office: ' + rfp['office'] + '\n' + \
            'Subject: ' + rfp['subject'] + '\n' + \
            'URL: ' + rfp['url'] + '\n' + \
            'Emails: ' + emails_str + '\n' + \
            'Description: ' + desc_str

            if check_for_phrases and check_for_agency:
                if check_desc(desc_str, phrases) and check_agency(rfp['agency'], agencies):
                    rfp_strings.append(rfp_str)
                else:pass
            elif check_for_phrases == True and check_for_agency == False:
                if check_desc(desc_str, phrases):
                    rfp_strings.append(rfp_str)
                else:pass
            elif check_for_phrases == False and check_for_agency == True:
                if check_agency(rfp['agency'], agencies):
                    rfp_strings.append(rfp_str)
                else:pass
            else:
                rfp_strings.append(rfp_str)


    message_field = '\n \n \n \n'.join(rfp_strings)

    return message_field
