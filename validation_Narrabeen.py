#==========================================================#
# Shoreline extraction from satellite images
#==========================================================#

# Kilian Vos WRL 2018

#%% 1. Initial settings

# load modules
import os
import numpy as np
import pickle
import warnings
warnings.filterwarnings("ignore")
import matplotlib.pyplot as plt
from matplotlib import gridspec
plt.ion()
import pandas as pd
from coastsat import SDS_download, SDS_preprocess, SDS_shoreline, SDS_tools, SDS_transects
from skimage import transform
import shutil
from datetime import datetime, timedelta
import pytz
from scipy import interpolate, stats

# region of interest (longitude, latitude in WGS84)
polygon = [[[151.301454, -33.700754],
            [151.311453, -33.702075],
            [151.307237, -33.739761],
            [151.294220, -33.736329],
            [151.301454, -33.700754]]]
# can also be loaded from a .kml polygon
# kml_polygon = os.path.join(os.getcwd(), 'examples', 'NARRA_polygon.kml')
# polygon = SDS_tools.polygon_from_kml(kml_polygon)
# convert polygon to a smallest rectangle (sides parallel to coordinate axes)       
polygon = SDS_tools.smallest_rectangle(polygon)

# date range
dates = ['1984-12-01', '2022-01-01']

# satellite missions
sat_list = ['L5','L7','L8','S2']
collection = 'C01' # choose Landsat collection 'C01' or 'C02'
# name of the site
sitename = 'NARRA'

# filepath where data will be stored
filepath_data = os.path.join(os.getcwd(), 'data')

# put all the inputs into a dictionnary
inputs = {
    'polygon': polygon,
    'dates': dates,
    'sat_list': sat_list,
    'sitename': sitename,
    'filepath': filepath_data,
    'landsat_collection': collection
        }

# before downloading the images, check how many images are available for your inputs
SDS_download.check_images_available(inputs);

#%% 2. Retrieve images

# only uncomment this line if you want Landsat Tier 2 images (not suitable for time-series analysis)
# inputs['include_T2'] = True

# retrieve satellite images from GEE
metadata = SDS_download.retrieve_images(inputs)

# if you have already downloaded the images, just load the metadata file
metadata = SDS_download.get_metadata(inputs) 

#%% 3. Batch shoreline detection
    
# settings for the shoreline extraction
settings = { 
    # general parameters:
    'cloud_thresh': 0.5,        # threshold on maximum cloud cover
    'output_epsg': 3857,        # epsg code of spatial reference system desired for the output   
    # quality control:
    'check_detection': True,    # if True, shows each shoreline detection to the user for validation
    'adjust_detection': False,  # if True, allows user to adjust the postion of each shoreline by changing the threhold
    'save_figure': True,        # if True, saves a figure showing the mapped shoreline for each image
    # [ONLY FOR ADVANCED USERS] shoreline detection parameters:
    'min_beach_area': 4500,     # minimum area (in metres^2) for an object to be labelled as a beach
    'buffer_size': 150,         # radius (in metres) of the buffer around sandy pixels considered in the shoreline detection
    'min_length_sl': 200,       # minimum length (in metres) of shoreline perimeter to be valid
    'cloud_mask_issue': False,  # switch this parameter to True if sand pixels are masked (in black) on many images  
    'sand_color': 'default',    # 'default', 'dark' (for grey/black sand beaches) or 'bright' (for white sand beaches)
    # add the inputs defined previously
    'inputs': inputs,
}

# [OPTIONAL] preprocess images (cloud masking, pansharpening/down-sampling)
SDS_preprocess.save_jpg(metadata, settings)

# [OPTIONAL] create a reference shoreline (helps to identify outliers and false detections)
settings['reference_shoreline'] = SDS_preprocess.get_reference_sl(metadata, settings)
# set the max distance (in meters) allowed from the reference shoreline for a detected shoreline to be valid
settings['max_dist_ref'] = 100        

# extract shorelines from all images (also saves output.pkl and shorelines.kml)
output = SDS_shoreline.extract_shorelines(metadata, settings)

#%% extra QA step to remove false detections and misclassified images 
# (useful when running the shoreline detection automatically)

# settings for discarding the badly classified images
settings['prc_pixel'] = 0.15    # minimum percentage of change from land to water for defining the wet/dry areas        
settings['prc_image'] = 0.3     # maximum percentage of misclassified pixels allowed in the image, otherwise discarded

# resize all the classified images to a fixed size so that all pixels overlap
height = [_.shape[0] for _ in output['im_classif']]
width = [_.shape[1] for _ in output['im_classif']]
heights = height.copy(); widths = width.copy()
if len(np.unique(height)) == 1: height = height[0]
else:
    counts = [sum(height == _) for _ in np.unique(height)]
    height = np.unique(height)[np.argmax(counts)]    
if len(np.unique(width)) == 1: width = width[0]    
else:
    counts = [sum(width == _) for _ in np.unique(width)]
    width = np.unique(width)[np.argmax(counts)]

# ignore images with completely different size
idx_skip_height = np.abs(np.array(heights) - height) > settings['prc_image']*height
idx_skip_width = np.abs(np.array(widths) - width) > settings['prc_image']*width
idx_skip = np.where(np.logical_or(idx_skip_height,idx_skip_width))[0]

# format images to same height and width
for k in range(len(output['im_classif'])):
    if k in idx_skip: continue
    # group land and water classes together (land = 0 , water = 1)
    output['im_classif'][k][output['im_classif'][k] == 1] = 0
    output['im_classif'][k][np.logical_or(output['im_classif'][k] == 3,
                                          output['im_classif'][k] == 2)] = 1
    # resize
    output['im_classif'][k] = transform.resize(output['im_classif'][k],
                                               (height, width), order=0,
                                                preserve_range=True,
                                                mode='constant')
                
# compute average probability of being land or water (can take some time)
im_av = np.empty((height,width))
for i in range(height):
    for j in range(width):
        pixel_values = []
        for k in range(len(output['im_classif'])):
            if (output['cloud_cover'][k] > 0.1) or (k in idx_skip):
                continue
            pixel_values.append(output['im_classif'][k][i,j])
        im_av[i,j] = np.nanmean(pixel_values)
        
# only consider the part of the image with high confidence 
# (large probability of belonging to land or water)
im_bin = np.logical_or(im_av > 1-settings['prc_pixel'], im_av < settings['prc_pixel'])
# do not consider the edges as they can be boundary effects
im_bin[:,[0,-1]] = False
im_bin[[0,-1],:] = False

# rearranges images in folders
source_folder = os.path.join(settings['inputs']['filepath'],settings['inputs']['sitename'],'jpg_files','detection')
dest_folder1 = os.path.join(settings['inputs']['filepath'],settings['inputs']['sitename'],'jpg_files','all_images')
dest_folder2 = os.path.join(settings['inputs']['filepath'],settings['inputs']['sitename'],'jpg_files','rejected')
if not os.path.exists(dest_folder2): os.makedirs(dest_folder2)
# copy all detections to new folder
shutil.copytree(source_folder,dest_folder1)
# remove erroneous classifications
idx_remove = []
for k in range(len(output['im_classif'])):
    # different size images
    if k in idx_skip:
        idx_remove.append(k)
        # store rejected image
        fn = output['filename'][k].split('_')[0] + '_' + output['filename'][k].split('_')[1] + '.jpg'
        file1 = os.path.join(dest_folder1,fn)
        file2 = os.path.join(dest_folder2,fn)
        if os.path.exists(file1):
            shutil.move(file1,file2)
        continue
    # calculate the difference
    im_diff = np.abs(np.round(im_av)-output['im_classif'][k])
    im_diff[~im_bin] = np.nan
    # calculate the percentage of pixels that are different
    prc_change = sum(sum(im_diff == 1))/sum(sum(im_bin))
    if prc_change > settings['prc_image']:
        # print('%.2f'%prc_change, end='..')
        idx_remove.append(k)
        # store rejected image
        fn = output['filename'][k].split('_')[0] + '_' + output['filename'][k].split('_')[1] + '.jpg'
        file1 = os.path.join(dest_folder1,fn)
        file2 = os.path.join(dest_folder2,fn)
        if os.path.exists(file1):
            shutil.move(file1,file2)
# print how many images were removed
print('%d / %d images different size'%(len(idx_skip),len(output['im_classif'])))
print('%d / %d images wrong classif'%(len(idx_remove)-len(idx_skip),len(output['im_classif'])))
print('%d%% images removed'%(100*len(idx_remove)/len(output['im_classif'])))
                  
# save figure for visual QA
fig,ax = plt.subplots(1,2,figsize=(15,8),sharex=True,sharey=True,tight_layout=True)
ax[0].set(title='Land-Water probability')
ax[0].axis('off')
ims = ax[0].imshow(1-im_av, cmap='coolwarm') 
cb = plt.colorbar(ims, ax=ax[0])
ax[1].set(title='Dry/wet pixels')
ax[1].axis('off')
ax[1].imshow(im_bin, cmap='gray')
ax[1].text(0.01,0.99,'%d/%d'%(len(idx_remove),len(output['im_classif'])),ha='left',va='top',
           transform=ax[1].transAxes, bbox=dict(boxstyle="square", ec='k',fc='w'))
fig.savefig(os.path.join(settings['inputs']['filepath'],settings['inputs']['sitename'], 'classif_qa.jpg'), dpi=200)
        
# clean up output dict (storing the classified image takes up a lot of space)
output.pop('im_classif')
idx_all = np.linspace(0, len(output['dates'])-1, len(output['dates']))
idx_keep = list(np.where(~np.isin(idx_all,idx_remove))[0])        
for key in output.keys():
    output[key] = [output[key][_] for _ in idx_keep]
# store output
filepath = os.path.join(inputs['filepath'], sitename)
with open(os.path.join(filepath, sitename + '_output' + '.pkl'), 'wb') as f:
    pickle.dump(output,f) 

#%% 4. Shoreline analysis

# if you have already mapped the shorelines, load the output.pkl file
filepath = os.path.join(inputs['filepath'], sitename)
with open(os.path.join(filepath, sitename + '_output' + '.pkl'), 'rb') as f:
    output = pickle.load(f) 

# remove duplicates (images taken on the same date by the same satellite)
output = SDS_tools.remove_duplicates(output)
# remove inaccurate georeferencing (set threshold to 10 m)
output = SDS_tools.remove_inaccurate_georef(output, 10)

# for GIS applications, save output into a GEOJSON layer
geomtype = 'lines' # choose 'points' or 'lines' for the layer geometry
gdf = SDS_tools.output_to_gdf(output, geomtype)
gdf.crs = {'init':'epsg:'+str(settings['output_epsg'])} # set layer projection
# save GEOJSON layer to file
gdf.to_file(os.path.join(inputs['filepath'], inputs['sitename'], '%s_output_%s.geojson'%(sitename,geomtype)),
                                driver='GeoJSON', encoding='utf-8')

# plot the mapped shorelines
plt.ion()
fig = plt.figure(figsize=[15,8], tight_layout=True)
plt.axis('equal')
plt.xlabel('Eastings')
plt.ylabel('Northings')
plt.grid(linestyle=':', color='0.5')
for i in range(len(output['shorelines'])):
    sl = output['shorelines'][i]
    date = output['dates'][i]
    plt.plot(sl[:,0], sl[:,1], '.', label=date.strftime('%d-%m-%Y'))
plt.legend() 

# load the transects from a .geojson file
geojson_file = os.path.join(os.getcwd(), 'examples', 'NARRA_transects.geojson')
transects = SDS_tools.transects_from_geojson(geojson_file)
   
# plot the transects to make sure they are correct (origin landwards!)
fig = plt.figure(figsize=[15,8], tight_layout=True)
plt.axis('equal')
plt.xlabel('Eastings')
plt.ylabel('Northings')
plt.grid(linestyle=':', color='0.5')
for i in range(len(output['shorelines'])):
    sl = output['shorelines'][i]
    date = output['dates'][i]
    plt.plot(sl[:,0], sl[:,1], '.', label=date.strftime('%d-%m-%Y'))
for i,key in enumerate(list(transects.keys())):
    plt.plot(transects[key][0,0],transects[key][0,1], 'bo', ms=5)
    plt.plot(transects[key][:,0],transects[key][:,1],'k-',lw=1)
    plt.text(transects[key][0,0]-100, transects[key][0,1]+100, key,
                va='center', ha='right', bbox=dict(boxstyle="square", ec='k',fc='w'))

# load the measured tide data
filepath = os.path.join(os.getcwd(),'examples','NARRA_tides.csv')
tide_data = pd.read_csv(filepath, parse_dates=['dates'])
dates_ts = [_.to_pydatetime() for _ in tide_data['dates']]
tides_ts = np.array(tide_data['tide'])
# get tide levels corresponding to the time of image acquisition
dates_sat = output['dates']
tides_sat = SDS_tools.get_closest_datapoint(dates_sat, dates_ts, tides_ts)

# intersect the transects with the 2D shorelines to obtain time-series of cross-shore distance
settings_transects = { # parameters for shoreline intersections
                      'along_dist':         25,             # along-shore distance to use for intersection
                      'max_std':            15,             # max std for points around transect
                      'max_range':          30,             # max range for points around transect
                      'min_val':            -100,           # largest negative value along transect (landwards of transect origin)
                      # parameters for outlier removal
                      'nan/max':            'auto',         # mode for removing outliers ('auto', 'nan', 'max')
                      'prc_std':            0.1,            # percentage to use in 'auto' mode to switch from 'nan' to 'max'
                      'max_cross_change':   40,             # two values of max_cross_change distance to use
                      'plot_fig':           False,          # whether to plot the intermediate steps
                      }
cross_distance = SDS_transects.compute_intersection(output, transects, settings_transects) 

# tidal correction along each transect
reference_elevation = 0.7 # elevation at which you would like the shoreline time-series to be
beach_slope = 0.1
cross_distance_tidally_corrected = {}
for key in cross_distance.keys():
    correction = (tides_sat-reference_elevation)/beach_slope
    cross_distance_tidally_corrected[key] = cross_distance[key] + correction
    
# remove outliers
cross_distance = SDS_transects.reject_outliers(cross_distance_tidally_corrected,output,settings_transects) 

# store the tidally-corrected time-series in a .csv file
out_dict = dict([])
out_dict['dates'] = dates_sat
for key in cross_distance_tidally_corrected.keys():
    out_dict['Transect '+ key] = cross_distance_tidally_corrected[key]
df = pd.DataFrame(out_dict)
fn = os.path.join(settings['inputs']['filepath'],settings['inputs']['sitename'],
                  'transect_time_series_tidally_corrected.csv')
df.to_csv(fn, sep=',')
print('Tidally-corrected time-series of the shoreline change along the transects saved as:\n%s'%fn)

# plot the time-series
fig = plt.figure(figsize=[15,8], tight_layout=True)
gs = gridspec.GridSpec(len(cross_distance),1)
gs.update(left=0.05, right=0.95, bottom=0.05, top=0.95, hspace=0.05)
for i,key in enumerate(cross_distance_tidally_corrected.keys()):
    if np.all(np.isnan(cross_distance_tidally_corrected[key])):
        continue
    ax = fig.add_subplot(gs[i,0])
    ax.grid(linestyle=':', color='0.5')
    ax.set_ylim([-50,50])
    median = np.nanmedian(cross_distance_tidally_corrected[key])
    ax.plot(output['dates'],
            cross_distance_tidally_corrected[key]-median,
            '-o', ms=6, mfc='w')
    ax.set_ylabel('distance [m]', fontsize=12)
    ax.text(0.5,0.95, key, bbox=dict(boxstyle="square", ec='k',fc='w'), ha='center',
            va='top', transform=ax.transAxes, fontsize=14) 
    
#%% 5. Comparison to groundtruth

# load groundtruth
filepath = os.path.join(os.getcwd(), 'examples')
with open(os.path.join(filepath, 'NARRA_groundtruth_07m' + '.pkl'), 'rb') as f:
    gt = pickle.load(f)
    
# convert timezone
for key in gt.keys():
    gt[key]['dates'] = [_.astimezone(pytz.utc) for _ in gt[key]['dates']]

sett = {'min_days':3,'max_days':10, 'binwidth':3, 'lims':[-50,50]} 
chain_sat_all = []
chain_sur_all = [] 
satnames_all = []  
for key in transects.keys():
    
    # remove nans
    chainage = cross_distance[key]
    idx_nan = np.isnan(chainage)
    dates_nonans = [output['dates'][k] for k in np.where(~idx_nan)[0]]
    satnames_nonans = [output['satname'][k] for k in np.where(~idx_nan)[0]]
    chain_nonans = chainage[~idx_nan]
    
    # calculate the mean shoreline position since 1987 from surveyed data
    # mean = np.nanmean(gt[key]['chainage'][[np.logical_and(_>dates_nonans[0],_<dates_nonans[-1]) for _ in gt[key]['dates']]])
    
    chain_sat_dm = chain_nonans
    chain_sur_dm = gt[key]['chainage']
    
    # plot the time-series
    fig= plt.figure(figsize=[15,8], tight_layout=True)
    gs = gridspec.GridSpec(2,3)
    ax0 = fig.add_subplot(gs[0,:])
    ax0.grid(which='major',linestyle=':',color='0.5')
    ax0.plot(dates_nonans, chain_sat_dm,'-')
    ax0.plot(gt[key]['dates'], chain_sur_dm, '-')
    ax0.set(title= 'Transect ' + key, xlim=[output['dates'][0]-timedelta(days=30),
                                           output['dates'][-1]+timedelta(days=30)])#,ylim=sett['lims'])
    
    # interpolate surveyed data around satellite data
    chain_int = np.nan*np.ones(len(dates_nonans))
    for k,date in enumerate(dates_nonans):
        # compute the days distance for each satellite date 
        days_diff = np.array([ (_ - date).days for _ in gt[key]['dates']])
        # if nothing within 10 days put a nan
        if np.min(np.abs(days_diff)) > sett['max_days']:
            chain_int[k] = np.nan
        else:
            # if a point within 3 days, take that point (no interpolation)
            if np.min(np.abs(days_diff)) < sett['min_days']:
                idx_closest = np.where(np.abs(days_diff) == np.min(np.abs(days_diff)))
                chain_int[k] = float(gt[key]['chainage'][idx_closest[0][0]])
            else: # otherwise, between 3 and 10 days, interpolate between the 2 closest points
                if sum(days_diff > 0) == 0:
                    break
                idx_after = np.where(days_diff > 0)[0][0]
                idx_before = idx_after - 1
                x = [gt[key]['dates'][idx_before].toordinal() , gt[key]['dates'][idx_after].toordinal()]
                y = [gt[key]['chainage'][idx_before], gt[key]['chainage'][idx_after]]
                f = interpolate.interp1d(x, y,bounds_error=True)
                chain_int[k] = float(f(date.toordinal()))
    
    # remove nans again
    idx_nan = np.isnan(chain_int)
    chain_sat = chain_nonans[~idx_nan]
    chain_sur = chain_int[~idx_nan]
    dates_sat = [dates_nonans[k] for k in np.where(~idx_nan)[0]]
    satnames = [satnames_nonans[k] for k in np.where(~idx_nan)[0]]
    chain_sat_all = np.append(chain_sat_all,chain_sat)
    chain_sur_all = np.append(chain_sur_all,chain_sur)
    satnames_all = satnames_all + satnames
    
    # error statistics
    slope, intercept, rvalue, pvalue, std_err = stats.linregress(chain_sur, chain_sat)
    R2 = rvalue**2 
    ax0.text(0,1,'R2 = %.2f'%R2,bbox=dict(boxstyle='square', facecolor='w', alpha=1),transform=ax0.transAxes)
    chain_error = chain_sat - chain_sur
    rmse = np.sqrt(np.mean((chain_error)**2))
    mean = np.mean(chain_error)
    std = np.std(chain_error)
    q90 = np.percentile(np.abs(chain_error), 90)
    
    # 1:1 plot
    ax1 = fig.add_subplot(gs[1,0])
    ax1.axis('equal')
    ax1.grid(which='major',linestyle=':',color='0.5')
    for k,sat in enumerate(list(np.unique(satnames))): 
        idx = np.where([_ == sat for _ in satnames])[0]
        ax1.plot(chain_sur[idx], chain_sat[idx], 'o', ms=4, mfc='C'+str(k),mec='C'+str(k), alpha=0.7, label=sat)
    ax1.legend(loc=4)
    ax1.plot([ax1.get_xlim()[0], ax1.get_ylim()[1]],[ax1.get_xlim()[0], ax1.get_ylim()[1]],'k--',lw=2)
    ax1.set(xlabel='survey [m]', ylabel='satellite [m]')

    # boxplots
    ax2 = fig.add_subplot(gs[1,1])
    data = []
    median_data = []
    n_data = []
    ax2.yaxis.grid()
    for k,sat in enumerate(list(np.unique(satnames))):
        idx = np.where([_ == sat for _ in satnames])[0]
        data.append(chain_error[idx])
        median_data.append(np.median(chain_error[idx]))
        n_data.append(len(chain_error[idx]))
    bp = ax2.boxplot(data,0,'k.', labels=list(np.unique(satnames)), patch_artist=True)
    for median in bp['medians']:
        median.set(color='k', linewidth=1.5)
    for j,boxes in enumerate(bp['boxes']):
        boxes.set(facecolor='C'+str(j))
        ax2.text(j+1,median_data[j]+1, '%.1f' % median_data[j], horizontalalignment='center', fontsize=8)
        ax2.text(j+1+0.35,median_data[j]+1, ('n=%.d' % int(n_data[j])), ha='center', va='center', fontsize=8, rotation='vertical')
    ax2.set(ylabel='error [m]', ylim=sett['lims'])
    
    # histogram
    ax3 = fig.add_subplot(gs[1,2])
    ax3.grid(which='major',linestyle=':',color='0.5')
    ax3.axvline(x=0, ls='--', lw=1.5, color='k')
    binwidth=sett['binwidth']
    bins = np.arange(min(chain_error), max(chain_error) + binwidth, binwidth)
    density = plt.hist(chain_error, bins=bins, density=True, color='0.6', edgecolor='k', alpha=0.5)
    mu, std = stats.norm.fit(chain_error)
    pval = stats.normaltest(chain_error)[1]
    xlims = ax3.get_xlim()
    x = np.linspace(xlims[0], xlims[1], 100)
    p = stats.norm.pdf(x, mu, std)
    ax3.plot(x, p, 'r-', linewidth=1)
    ax3.set(xlabel='error [m]', ylabel='pdf', xlim=sett['lims'])   
    str_stats = ' rmse = %.1f\n mean = %.1f\n std = %.1f\n q90 = %.1f' % (rmse, mean, std, q90) 
    ax3.text(0, 0.98, str_stats,va='top', transform=ax3.transAxes)
    
    fig.savefig('transect_' + key + '.jpg', dpi=150)
    
# calculate statistics for all transects together
chain_error = chain_sat_all - chain_sur_all        
slope, intercept, rvalue, pvalue, std_err = stats.linregress(chain_sur, chain_sat) 
R2 = rvalue**2
rmse = np.sqrt(np.mean((chain_error)**2))
mean = np.mean(chain_error)
std = np.std(chain_error)
q90 = np.percentile(np.abs(chain_error), 90)

fig,ax = plt.subplots(1,2,figsize=(15,5), tight_layout=True)
# histogram
ax[0].grid(which='major',linestyle=':',color='0.5')
ax[0].axvline(x=0, ls='--', lw=1.5, color='k')
binwidth=sett['binwidth']
bins = np.arange(min(chain_error), max(chain_error) + binwidth, binwidth)
density = ax[0].hist(chain_error, bins=bins, density=True, color='0.6', edgecolor='k', alpha=0.5)
mu, std = stats.norm.fit(chain_error)
pval = stats.normaltest(chain_error)[1]
xlims = ax3.get_xlim()
x = np.linspace(xlims[0], xlims[1], 100)
p = stats.norm.pdf(x, mu, std)
ax[0].plot(x, p, 'r-', linewidth=1)
ax[0].set(xlabel='error [m]', ylabel='pdf', xlim=sett['lims'])   
str_stats = ' rmse = %.1f\n mean = %.1f\n std = %.1f\n q90 = %.1f' % (rmse, mean, std, q90) 
ax[0].text(0, 0.98, str_stats,va='top', transform=ax[0].transAxes)

# boxplot
data = []
median_data = []
n_data = []
ax[1].yaxis.grid()
for k,sat in enumerate(list(np.unique(satnames_all))):
    idx = np.where([_ == sat for _ in satnames_all])[0]
    data.append(chain_error[idx])
    median_data.append(np.median(chain_error[idx]))
    n_data.append(len(chain_error[idx]))
bp = ax[1].boxplot(data,0,'k.', labels=list(np.unique(satnames_all)), patch_artist=True)
for median in bp['medians']:
    median.set(color='k', linewidth=1.5)
for j,boxes in enumerate(bp['boxes']):
    boxes.set(facecolor='C'+str(j))
    ax[1].text(j+1,median_data[j]+1, '%.1f' % median_data[j], horizontalalignment='center', fontsize=8)
    ax[1].text(j+1+0.35,median_data[j]+1, ('n=%.d' % int(n_data[j])), ha='center', va='center', fontsize=8, rotation='vertical')
ax[1].set(ylabel='error [m]', ylim=sett['lims'])

fig.savefig('all_transects_stats' + '.jpg', dpi=150)

