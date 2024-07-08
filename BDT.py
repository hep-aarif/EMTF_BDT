#from ROOT import * 

import ROOT

import gc

#import uproot
from subprocess import Popen, PIPE
import numpy as np
import os
from Compressor import Compressor
from Run3_Variables import *
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_squared_error
import psutil
from multiprocessing import Pool
import argparse
from to_TVMA import convert_model
import datetime

from math import log
from math import exp

from array import array

parser = argparse.ArgumentParser()
parser.add_argument("-n", "--num_jobs", required=False)
parser.add_argument("-i", "--index", required = False)
parser.add_argument("-m", "--mode", required = True)
parser.add_argument("-g", "--gem", required = True)
parser.add_argument("-s", "--slope", required = True)
args = parser.parse_args()


MODE = int(args.mode)
GEM = (int(args.gem) == 1)
USE_SLOPE = (int(args.slope) == 1)

now=datetime.datetime.now()
date_time=now.strftime("%Y%m%d_%H%M")

label = date_time

if(GEM):
    label += "_GEM"

print(MODE)
print(GEM)
print(USE_SLOPE)

#USE_SLOPE = True
USE_AGRESSIVE_SLOPE = False


MAX_FILE = 10 #20
MAX_EVT = 10000
DEBUG = False
PRNT_EVT = 1000

#folders = ["/afs/cern.ch/user/n/nhurley/CMSSW_12_3_0/src/EMTF_MC_NTuple_SingleMu_new_neg.root", "/afs/cern.ch/user/n/nhurley/CMSSW_12_3_0/src/EMTF_MC_NTuple_SingleMu_pos_new.root"]
#base_dirs = ["/eos/user/n/nhurley/SingleMu/SingleMuFlatOneOverPt1To1000GeV_Ntuple_fixed__negEndcap_v2/221215_114244/0000/", "/eos/user/n/nhurley/SingleMu/SingleMuFlatOneOverPt1To1000GeV_Ntuple_fixed__posEndcap_v2/221215_111111/"]

#base_dirs = ["/eos/cms/store/user/eyigitba/emtf/L1Ntuples/Run3/BDT/inputNtuples/"]

base_dirs = ["/eos/cms/store/user/eyigitba/emtf/L1Ntuples/Run3/crabOut/CRAB_PrivateMC/SingleMuGun_flatOneOverPt1to1000_negEndcap_13_3_1_BDT2024_noGEM_10M/240112_135506/0000/", "/eos/cms/store/user/eyigitba/emtf/L1Ntuples/Run3/crabOut/CRAB_PrivateMC/SingleMuGun_flatOneOverPt1to1000_posEndcap_13_3_1_BDT2024_noGEM_10M/240112_152929/0000/"]

if(GEM):
    base_dirs = ["/eos/cms/store/user/eyigitba/emtf/L1Ntuples/Run3/crabOut/CRAB_PrivateMC/SingleMuGun_flatOneOverPt1to1000_negEndcap_13_3_1_BDT2024_GEMILT_10M/240112_152811/0000/", "/eos/cms/store/user/eyigitba/emtf/L1Ntuples/Run3/crabOut/CRAB_PrivateMC/SingleMuGun_flatOneOverPt1to1000_posEndcap_13_3_1_BDT2024_GEMILT_10M/240113_114237/0000/"]

#station-station transitions for delta phi's and theta's
transitions = ["12", "13", "14", "23", "24", "34"]
#modes we want to analyze
EMTF_MODES = [15, 14, 13, 12, 11, 10, 9, 7, 6, 5, 3]


features_collection = []

#map station to the indices of transitions (12, 13, 14, 23, 24, 34)
#                                          (0,   1,  2,  3,  4,  5)
station_transition_map = {
            1:[0, 1, 2],
            2:[0, 3, 4],
            3:[1, 3, 5],
            4: [2, 4, 5]}

evt_tree  = ROOT.TChain('EMTFNtuple/tree')

#recursivelh access different subdirectories of given folder from above
file_list = []
for base_dir in base_dirs:
    nFiles = 0
    break_loop = False
    for dirname, dirs, files in os.walk(base_dir):
        if break_loop: break
        for file in files:
            if break_loop: break
            if not '.root' in file: continue
            file_name = "%s/%s" % (dirname, file)
            nFiles   += 1
            print ('* Loading file #%s: %s' % (nFiles, file))
            evt_tree.Add(file_name)
            if nFiles >= MAX_FILE/2: break_loop = True

#Flag for breaking loop if we hit max file limit
break_loop = False

#Data frame containing a list of these feature dictionaries, columns are features, rows are different tracks
X = pd.DataFrame()

X_dict = {}

Y = np.array([])
W = np.array([])

Y_eta = np.array([])
Y_phi = np.array([])
Y_pt = np.array([])
#we will want to break the loop when debugging and look at a single entry
event_break = False    
#loop through all events in the input file

nNegEndcap = 0
nPosEndcap = 0

for event in range(evt_tree.GetEntries()):

    if event_break: break
    #if event == MAX_EVT: break
    if event == MAX_EVT and nNegEndcap != 0 and nPosEndcap != 0: 
        break

    if event % PRNT_EVT == 0:
        print('BDT.py: Processing Event #%d' % (event))
        print('Pos-Endcap',nPosEndcap)
        print('Neg-Endcap',nNegEndcap)    
    evt_tree.GetEntry(event)


    if(nNegEndcap > MAX_EVT/2 and evt_tree.genPart_eta[0] <= 0):
        continue
    elif(nPosEndcap > MAX_EVT/2 and evt_tree.genPart_eta[0] > 0):
        continue

    #features per track that will be used as inputs to the BDT
    features = Compressor()

    #look at every track in the input file, for-else lol sorry
    track = -1
    for select_track in range(evt_tree.emtfTrack_size):    
        mode = evt_tree.emtfTrack_mode[select_track]
        if mode == MODE or mode == 15: 
            track = select_track
            break

    if track == -1: continue
    features["mode"] = MODE #mode

    #only accept the mode we want to train
    #if not mode == MODE: break

    #convert mode to station bit-array representation
    station_isPresent = np.unpackbits(np.array([mode], dtype='>i8').view(np.uint8))[-4:]
    if(MODE == 9 and station_isPresent[0] and station_isPresent[3]):
        station_isPresent = np.unpackbits(np.array([9], dtype='>i8').view(np.uint8))[-4:]
    elif(MODE == 14 and station_isPresent[0] and station_isPresent[1] and station_isPresent[2]):
        station_isPresent = np.unpackbits(np.array([14], dtype='>i8').view(np.uint8))[-4:]
    elif(MODE == 13 and station_isPresent[0] and station_isPresent[1] and station_isPresent[3]):
        station_isPresent = np.unpackbits(np.array([13], dtype='>i8').view(np.uint8))[-4:]
    elif(MODE == 11 and station_isPresent[0] and station_isPresent[2] and station_isPresent[3]):
        station_isPresent = np.unpackbits(np.array([11], dtype='>i8').view(np.uint8))[-4:]
    elif(MODE != mode):
        continue

    #define station patterns
    station_pattern = []
    for station in range(4):
        hitref = eval('evt_tree.emtfTrack_hitref%d[%d]' % (station + 1, track))
        features['ph%d' % (station + 1)] = evt_tree.emtfHit_emtf_phi[hitref]
        features['th%d' % (station + 1)] = evt_tree.emtfHit_emtf_theta[hitref]
        if station_isPresent[station]:
            pattern = evt_tree.emtfTrack_ptLUT_cpattern[track][station]
            if not "theta" in features.keys() and station != 0:
                features["theta"] = evt_tree.emtfHit_emtf_theta[hitref]
            for station2 in range(station + 1, 4):
                if station_isPresent[station2]:
                    hitref2 = eval('evt_tree.emtfTrack_hitref%d[%d]' % (station2 + 1, track))
                    features['dTh_' + str(station + 1) + str(station2 + 1)] = evt_tree.emtfHit_emtf_theta[hitref2] - evt_tree.emtfHit_emtf_theta[hitref]
            features['RPC_' + str(station + 1)] = 1 if pattern == 0 else 0 #evt_tree.emtfHit_type[hitref] == 2  maybe?
            if((evt_tree.emtfHit_run3_pattern[hitref] != -99 and USE_SLOPE) or USE_AGRESSIVE_SLOPE):
                features['bend_' + str(station + 1)] = evt_tree.emtfHit_slope[hitref]
        else:
            pattern = -99
        station_pattern.append(pattern)
        features['pattern_' + str(station + 1)] = pattern
        features['presence_' + str(station + 1)] = station_isPresent[station]

    features['endcap'] = evt_tree.emtfTrack_endcap[track]

    #scalar features
    
    features["st1_ring2"] = evt_tree.emtfTrack_ptLUT_st1_ring2[track]
    #vector features by station
    for station, pattern in enumerate(station_pattern):
        hitref = eval('evt_tree.emtfTrack_hitref%d[%d]' % (station + 1, track))
        if pattern == -99 or pattern == 10: bend = 0
        if not station_isPresent[station]: continue
        elif pattern % 2 == 0: bend = (10 - pattern) / 2
        elif pattern % 2 == 1: bend = -1 * (11 - pattern) / 2

        if evt_tree.emtfTrack_endcap[track] == 1: bend *= -1

        if((evt_tree.emtfHit_run3_pattern[hitref] == -99 or not USE_SLOPE) and not USE_AGRESSIVE_SLOPE):
            features["bend_" + str(station + 1)] = bend
            if features['RPC_' + str(station + 1)] and abs(features["bend_" + str(station + 1)]) == 5: features["bend_" + str(station + 1)] = 0
        else:
            features["old_bend_" + str(station + 1)] = bend
            if features['RPC_' + str(station + 1)] and abs(features["old_bend_" + str(station + 1)]) == 5: features["old_bend_" + str(station + 1)] = 0
        
        features["FR_" + str(station + 1)] = evt_tree.emtfTrack_ptLUT_fr[track][station]

        #Fix RPC bend

    #features with station-station transitions
    for i, transition in enumerate(transitions):
        sign = 1 if evt_tree.emtfTrack_ptLUT_signPh[track][i] else -1
        features["dPhi_" + str(transition)] = evt_tree.emtfTrack_ptLUT_deltaPh[track][i] * sign
    
    #clean-up transitions involving not present stations, is this unecessary??
    for i, station_isPresent in enumerate(station_isPresent):
        if not station_isPresent:
            for transition in station_transition_map[i + 1]:
                features["dPhi_" + transitions[transition]] = -999
                features["dTh_" + transitions[transition]] = -999

    for i in range(4):
        if features['presence_' + str(i + 1)]:
            for transition in station_transition_map[i + 1]:
                if features["dPhi_" + str(transitions[transition])] != -999 and "signPhi" not in features.keys():
                    features["signPhi"] = 1 if features["dPhi_" + str(transitions[transition])] >= 0 else -1
                    break

    for i in ['12', '13', '14', '23', '24', '34']:
        features['dPhi_' + str(i)] *= features['signPhi']

    if DEBUG and mode == MODE:
        for k, v in features.items():
            print(k + " = " + str(v))

    #print("\nCompressing...\n")
    features_precompressed = {k:v for k, v in features.items()}
    #features.compress()
    
    if mode == 15:
        #Get dphi sums, must happen post-compression
        deltaPh_list = [features['dPhi_' + i] for i in transitions]
        features["dPhiSum4"] = sum(deltaPh_list)
        features["dPhiSum4A"] = np.sum(np.abs(deltaPh_list))

        station_deviation = []
        for i in range(4):
            station_deviation += [sum([np.abs(deltaPh_list[transition]) for transition in station_transition_map[i + 1]])]

        outStPh = np.where(station_deviation == max(station_deviation))[0][0] + 1

        if len(np.where(station_deviation == max(station_deviation))[0]) > 1: outStPh = 0

        features["outStPhi"] = outStPh

        if outStPh == 0: outStPh = 1
        
        other_transitions = [i for i in range(6) if i not in station_transition_map[outStPh]]
        features["dPhiSum3"] = sum([deltaPh_list[transition] for transition in other_transitions])
        features["dPhiSum3A"] = sum([abs(deltaPh_list[transition]) for transition in other_transitions])

    if DEBUG and mode == MODE:
        for k, v in features.items():
            print(k + " = " + str(v))

    if(evt_tree.genPart_eta[0] > 0):
        nPosEndcap = nPosEndcap + 1
    if(evt_tree.genPart_eta[0] <= 0):
        nNegEndcap = nNegEndcap + 1

    #for i in range(1, 5):
    #    if(features["bend_" + str(i)]):
    #        features["bend_" + str(i)] = features_precompressed["bend_" + str(i)]
    x_ = {}
    for key in Run3TrainingVariables[str(MODE)]:
        x_[key] = features[key]
    if DEBUG:
        with open("/afs/cern.ch/user/n/nhurley/CMSSW_12_3_0/src/EMTFPtAssign2017/inputs.txt", 'r') as compare:
            old_BDT_props = {}
            for i, line in enumerate(compare.readlines()):
                
                prop = line.split(":")[0].strip()

                if "New Track" in line: continue
                value = line.split(":")[-1].strip()
                old_BDT_props[prop] = float(value)

                if "TRK_hit_ids"  in prop: 
                    if old_BDT_props['ph1'] == features['ph1'] and old_BDT_props['ph2'] == features['ph2'] and old_BDT_props['ph3'] == features['ph3'] and old_BDT_props['ph4'] == features['ph4']:
                        matched = True
                        for k, v in x_.items():
                            if v != old_BDT_props[k]:
                                matched = False
                                print("Key: %s, old != new (%d, %d)" % (k, old_BDT_props[k], v))
                        if not matched:
                            print(features_precompressed)
                            print(features)
                            input('check this out!')
                        else: break
                        old_BDT_props = {}
            else:
                print("No match exists!")


    #print(X)


    for key in Run3TrainingVariables[str(MODE)]:
        if(key in X_dict.keys()):
            X_dict[key].append(x_[key])
        else:
            X_dict[key] = [x_[key]]


    if("GEN_pt" in X_dict.keys()):
        X_dict["GEN_pt"].append(evt_tree.genPart_pt[0])
    else:
        X_dict["GEN_pt"] = [evt_tree.genPart_pt[0]]

    #X = X.append(x_, ignore_index = True)
    X = pd.concat([X,pd.DataFrame([x_])], ignore_index = True)
    Y = np.append(Y, ROOT.log(evt_tree.genPart_pt[0]))
    Y_pt = np.append(Y_pt, evt_tree.genPart_pt[0])
    Y_eta = np.append(Y_eta, evt_tree.genPart_eta[0])
    Y_phi = np.append(Y_phi, evt_tree.genPart_phi[0])
    W = np.append(W, 1. / (ROOT.log(evt_tree.genPart_pt[0] + 0.000001)/ROOT.log(2)))

seed = 1234



import pickle

with open('EMTF_ntuple_slimmed.pickle', 'wb') as handle:
    pickle.dump(X_dict, handle, protocol=pickle.HIGHEST_PROTOCOL)


gc.collect()

#if(USE_SLOPE):
#    seed = 123
#if(USE_AGRESSIVE_SLOPE):
#    seed = 2020

X_train, X_test, Y_train, Y_test, W_train, W_test = train_test_split(X, Y, W, test_size=.5, random_state=seed)
X_train_2, X_test_2, Y_train_pt, Y_test_pt, W_train_2, W_test_2 = train_test_split(X, Y_pt, W, test_size=.5, random_state=seed)
X_train_2, X_test_2, Y_train_eta, Y_test_eta, W_train_2, W_test_2 = train_test_split(X, Y_eta, W, test_size=.5, random_state=seed)
X_train_3, X_test_3, Y_train_phi, Y_test_phi, W_train_3, W_test_3 = train_test_split(X, Y_phi, W, test_size=.5, random_state=seed)
dtrain = xgb.DMatrix(data = X_train, label = Y_train, weight = W_train)
dtest = xgb.DMatrix(data = X_test, label = Y_test, weight = W_test)

xg_reg = xgb.XGBRegressor(objective = 'reg:linear', 
                        learning_rate = .1, 
                        max_depth = 5, 
                        n_estimators = 400,
                        max_bins = 1000,
                        nthread = 30)

print("WAS HERE")

xg_reg.fit(X_train, Y_train, sample_weight = W_train)

print("WAS NOT HERE")

img_dict = xg_reg.get_booster().get_score(importance_type='weight')
img_total = 0
for key in img_dict.keys():
    img_total += img_dict[key]

print("Importance:")
for key in sorted(img_dict):
    print(key + ": " + str(float(img_dict[key])/img_total))

#try: outfile = TFile("./test_newSlope.root", 'recreate')
#except: outfile = TFile("./test_newSlope.root", 'create')

f_name = "./EMTF_BDT_" + label + "_TestTree_" + str(MODE) + ".root"
if(USE_SLOPE):
    f_name = "./EMTF_BDT_" + label + "_TestTree_newSlope_" + str(MODE) + ".root"
if(USE_AGRESSIVE_SLOPE):
    f_name =  "./EMTF_BDT_" + label + "_TestTree_newSlopeAggressive_" + str(MODE) + ".root"

try: outfile = ROOT.TFile(f_name, 'recreate')
except: outfile = ROOT.TFile(f_name, 'create')
scale_pt_temp = [0, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 20, 22, 25, 30, 35, 45, 60, 75, 100, 140, 160, 180, 200, 250, 300, 500, 1000] #high-pt range
scale_pt_temp_2 = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 32, 34, 36, 38, 40, 42, 44, 46, 48, 50, 55, 60] #low-pt range
scale_pt_2  = np.array(scale_pt_temp_2, dtype = 'float64')
scale_pt  = np.array(scale_pt_temp, dtype = 'float64')

h_pt  = ROOT.TH1D('h_pt_den_EMTF',  '', len(scale_pt_temp) - 1,  scale_pt)
h_pt_trg = ROOT.TH1D('h_pt_num_EMTF',  '', len(scale_pt_temp)-1,  scale_pt)

h_pt_2  = ROOT.TH1D('h_pt_den_EMTF_2',  '', len(scale_pt_temp_2) - 1,  scale_pt_2)
h_pt_trg_2 = ROOT.TH1D('h_pt_num_EMTF_2',  '', len(scale_pt_temp_2)-1,  scale_pt_2)

tree = ROOT.TTree("TestTree","TestTree")

pt_BDT = array('d', [0])
pt_GEN = array('d', [0])
pt_GEN_2 = array('d', [0])
eta_GEN = array('d', [0])
phi_GEN = array('d', [0])
tree.Branch('pt_BDT', pt_BDT, 'pt_BDT/D')
tree.Branch('pt_GEN', pt_GEN, 'pt_GEN/D')
tree.Branch('pt_GEN_2', pt_GEN, 'pt_GEN_2/D')
tree.Branch('eta_GEN', eta_GEN, 'eta_GEN/D')
tree.Branch('phi_GEN', phi_GEN, 'phi_GEN/D')



preds = xg_reg.predict(X_test)
for i, y in enumerate(Y_test):
    pt_real = exp(y)
    pt_pred = exp(preds[i])
    pt_BDT[0] = float(pt_pred)
    pt_GEN[0] = float(pt_real)
    eta_GEN[0] = float(Y_test_eta[i])
    phi_GEN[0] = float(Y_test_phi[i])
    pt_GEN_2[0] = float(Y_test_pt[i])
    tree.Fill()
    
    h_pt.Fill(pt_real)
    h_pt_2.Fill(pt_real)
    if pt_pred > 22:
        h_pt_trg.Fill(pt_real)
        h_pt_trg_2.Fill(pt_real)

tree.Write()

h_pt_trg.Divide(h_pt)
h_pt_trg.Write()

h_pt_trg_2.Divide(h_pt_2)
h_pt_trg_2.Write()
del outfile

rmse = np.sqrt(mean_squared_error(Y_test, preds))

print("RMSE: %f" % (rmse))


#input_vars = [(x, 'I') for x in X.head()]
#for idx, tree in enumerate(xg_reg.get_booster().get_dump()):
#    convert_model([tree],itree = idx,input_variables = input_vars, output_xml=f'/afs/cern.ch/user/n/nhurley/BDT/{MODE}/{idx}.xml')
