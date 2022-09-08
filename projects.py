import ee
import os
import numpy as np
import pickle
import warnings
warnings.filterwarnings("ignore")
import matplotlib
# matplotlib.use('Qt5Agg')
import matplotlib.pyplot as plt
from matplotlib import gridspec
plt.ion()
import pandas as pd
from datetime import datetime
from coastsat import SDS_download, SDS_preprocess, SDS_shoreline, SDS_tools, SDS_transects


def hydrata_authenticate():
    service_account = 'django-coastsat@hydrata-coastsat.iam.gserviceaccount.com'
    credentials = ee.ServiceAccountCredentials(service_account, './coastsat/hydrata-coastsat-3b8ff887df07.json')
    ee.Initialize(credentials)


def make_springfield():
    hydrata_authenticate()
    # region of interest (longitude, latitude)
    polygon = [
          [
            [
              -89.70577239990234,
              39.66458337842706
            ],
            [
              -89.64028358459473,
              39.66458337842706
            ],
            [
              -89.64028358459473,
              39.7209858007651
            ],
            [
              -89.70577239990234,
              39.7209858007651
            ],
            [
              -89.70577239990234,
              39.66458337842706
            ]
          ]
        ]
    # it's recommended to convert the polygon to the smallest rectangle (sides parallel to coordinate axes)
    polygon = SDS_tools.smallest_rectangle(polygon)
    # date range
    dates = ['2022-05-14', '2022-07-01']
    # satellite missions ['L5','L7','L8','L9','S2']
    sat_list = ['S2']
    # choose Landsat collection 'C01' or 'C02'
    collection = 'C01'
    # name of the site
    sitename = 'SPRINGFIELD'
    # directory where the data will be stored
    filepath = os.path.join(os.getcwd(), 'data')
    # put all the inputs into a dictionnary
    inputs = {'polygon': polygon, 'dates': dates, 'sat_list': sat_list, 'sitename': sitename, 'filepath':filepath,
             'landsat_collection': collection}

    # before downloading the images, check how many images are available for your inputs
    im_dict_T1, im_dict_T2  = SDS_download.check_images_available(inputs)
    print(f"{im_dict_T1=}")
    print(f"{im_dict_T2=}")
    metadata = SDS_download.retrieve_images(inputs)
    print(f"{metadata=}")
