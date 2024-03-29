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
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, column_index_from_string
warnings.filterwarnings("ignore", category=UserWarning, module='bs4')

logger = logging.getLogger(__name__)

from send_email import send_email
from fbo_ftp_scraper import *
from scrapers import *

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

from functools import reduce

from run import run
from tools.classes import RSSParser
import feedparser
from flask import Flask, render_template, request, send_file
app = Flask(__name__)

###############################################################################
###############################################################################
###############################################################################

BASE_DIR = os.getcwd()
DATA_DIR = os.path.join(BASE_DIR, 'data')
if not os.path.exists(DATA_DIR):
    os.mkdir(DATA_DIR)

def get_directory_structure(rootdir):
    """
    Creates a nested dictionary that represents the folder structure of rootdir
    """
    dir = {}
    rootdir = rootdir.rstrip(os.sep)
    start = rootdir.rfind(os.sep) + 1
    for path, dirs, files in os.walk(rootdir):
        folders = path[start:].split(os.sep)
        subdir = dict.fromkeys(files)
        parent = reduce(dict.get, folders[:-1], dir)
        parent[folders[-1]] = subdir
    return dir

def reset_key_words():

    os.chdir(DATA_DIR)

    if not os.path.exists('key_words.txt'):

        return None

    else:

        with open('key_words.txt', 'w') as f:

            f.write('')

    os.chdir(BASE_DIR)

    return

def write_word(word):

    os.chdir(DATA_DIR)

    if not os.path.exists('key_words.txt'):

        with open('key_words.txt', 'w') as f:

            f.write(word + '\n')

    else:

        with open('key_words.txt', 'a') as f:

            f.write(word + '\n')

    os.chdir(BASE_DIR)

    return None

def read_words():

    os.chdir(DATA_DIR)

    if not os.path.exists('key_words.txt'):

        with open('key_words.txt', 'w') as f:

            f.write(word + '\n')
        return 0

    else:

        with open('key_words.txt', 'r') as f:

            lines = set(f.readlines())

    os.chdir(BASE_DIR)

    return lines


###############################################################################
###############################################################################
###############################################################################



@app.route("/", methods=['POST','GET'])
def index():

    template_data = dict()

    data_dir_structure = data_dir_structure = get_directory_structure(DATA_DIR)
    data_dir_structure = data_dir_structure.get('data')
    template_data['data_dir_structure'] = data_dir_structure
    template_data['data_dir_len'] = len(data_dir_structure)
    template_data['data_url'] = DATA_DIR

    template_data['selection'] = 'All Files'


    try:
        last_run_row = pd.read_excel(os.path.join(DATA_DIR, 'history.xlsx'), 'Runs').iloc[-1]
        last_run = last_run_row['Time Run']
    except IndexError:
        last_run = 'Last run: Never'

    template_data['last_run'] = last_run

    dropdown_list = ['All Files', 'Dashboard', 'File Downloads', 'Run History', 'Update Files']
    template_data['dropdown_list'] = dropdown_list

    if request.method == 'POST':

        if request.form.get('dropdown-selection'):

            selection = request.form.get('dropdown-selection')
            template_data['selection'] = selection

            if selection == 'Dashboard':

                rfp_areas = [rfp for rfp in data_dir_structure.keys() if rfp != 'history.xlsx']
                template_data['rfp_areas'] = rfp_areas

            elif selection == 'File Downloads':

                pdf_downloads = pd.read_excel('data/History.xlsx', 'PDF Downloads')
                most_recent_pdfs_first_df = pdf_downloads.sort_values('Time Saved', ascending=False)
                most_recent_pdfs_first_df.drop('Change Type', axis=1, inplace=True)
                #[time_val, pdf_name, location]
                pdf_recent_rows = []
                pdf_old_rows = []
                for row in most_recent_pdfs_first_df.iterrows():
                    if row[1][0] > last_run:
                        pdf_recent_rows.append([row[1][0], row[1][1], row[1][2]])
                    else:
                        pdf_old_rows.append([row[1][0], row[1][1], row[1][2]])
                template_data['pdf_recent_rows'] = pdf_recent_rows
                template_data['len_pdf_recent_rows'] = len(pdf_recent_rows)
                template_data['pdf_old_rows'] = pdf_old_rows
                template_data['len_pdf_old_rows'] = len(pdf_old_rows)

                excel_downloads = pd.read_excel('data/History.xlsx', 'Excel Downloads')
                most_recent_excel_first_df = excel_downloads.sort_values('Time Saved', ascending=False)
                most_recent_excel_first_df.drop('Change Type', axis=1, inplace=True)
                #[time_val, pdf_name, location]
                excel_recent_rows = []
                excel_old_rows = []
                for row in most_recent_excel_first_df.iterrows():
                    if row[1][0] > last_run:
                        excel_recent_rows.append([row[1][0], row[1][1], row[1][2]])
                    else:
                        excel_old_rows.append([row[1][0], row[1][1], row[1][2]])
                template_data['excel_recent_rows'] = excel_recent_rows
                template_data['len_excel_recent_rows'] = len(excel_recent_rows)




            elif selection == 'Update Files':
                run()


    return render_template('index.html', **template_data)

@app.route("/dashboard", methods=['POST','GET'])
def dashboard():

    template_data = dict()

    agencies = ['Department of Energy']
    template_data['agencies'] = agencies

    data_dir_structure = data_dir_structure = get_directory_structure(DATA_DIR)
    data_dir_structure = data_dir_structure.get('data')

    rfp_areas = [rfp for rfp in data_dir_structure.keys() if rfp not in ['history.xlsx', 'key_words.txt']]
    template_data['rfp_areas'] = rfp_areas

    if request.method == 'POST':

        write_word(request.form.get('word'))

        words = read_words()

        template_data['words'] = words

        template_data['words_len'] = len(words)

    else:

        reset_key_words()

        template_data['words_len'] = 0


    return render_template('dashboard.html', **template_data)

if __name__ == "__main__":


    app.run()
