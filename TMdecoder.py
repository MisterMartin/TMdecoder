import struct
import xmltodict
import csv
import io
import sys
import argparse
import numpy as np
import glob as glob
from datetime import datetime
from datetime import timezone
from sys import exit

def RS41_RH_wvmr(TC_ambient,hPa_ambient,rh_reported,TC_humSensor):
    '''
    Parameters: ambient:tempC,prshPa, RH_reported, tempC_of humSensor
    Returns: ambient RH and ambient water vapor mixing ratio ppmv
    '''
    eswhPa_humSensor_temp=Hardy_1998(TC_humSensor)
    eswhPa_ambient_temp=Hardy_1998(TC_ambient)
    ew_hPa=eswhPa_humSensor_temp*rh_reported/100.
    RH_ambient=ew_hPa/eswhPa_ambient_temp*100
    WV_ppmv=WV_mixing_ratio(ew_hPa,hPa_ambient)
    return [RH_ambient,WV_ppmv]

def WV_mixing_ratio(ew_hPa,prshPa):
    '''
    Parameters: vapor pressure of water, ambient pressure 
    calculates water vapor mixing ratio (ppm) in both mass (ppmm) and volume(ppmv)
    Returns: WV_ppmv 
    '''
    molecw_air=28.97
    molecw_h2o=18.0
    epsilon=molecw_h2o/molecw_air
    WV_ppmm=epsilon*ew_hPa/(prshPa-ew_hPa)*1e6
    WV_ppmv=ew_hPa/(prshPa-ew_hPa)*1e6
    return WV_ppmv

def Hardy_1998(TC):
   '''
   Returns saturation vapor pressure in hPa esw_hPa at TC from Hardy (1998)
   Parameters Temp C
   This is the formulation used by Vaisala the maker of the RS41
   '''
   HC=[-2.8365744e3,-6.028076559e3,1.954263612e1,-2.737830188e-2,1.6261698e-5,7.0229056e-10,-1.8680009e-13]
   TK=TC+273.15
   i=0
   lesw=0
   for c in HC:
       lesw=lesw+c*TK**(i-2)
       i=i+1
   lesw=lesw+2.7150305*np.log(TK)
   esw_hPa=np.exp(lesw)/100
   return esw_hPa

class TMmsg:
    def __init__(self, msg_filename:str):
        '''
        Base class for Strateole2 TM message decoding

        See the Zephyr TM specification in:
        ZEPHYR INTERFACES FOR HOSTED INSTRUMENTS
        STR2-ZEPH-DCI-0-031 Version : 1.3

        Args:
            data (bytes): The binary data to decode.

        Examples:
            tm_msg = TMmsg(data)
            tm_msg.TMxml()
            tm_msg.CRCxml()
        '''
        with open(msg_filename, "rb") as binary_file:
            data = binary_file.read()

        self.data = data
        self.bindata = self.binaryData()        
        self.unix_end_time = self.timeStamp()
        date_time = datetime.fromtimestamp(int(self.unix_end_time),tz=timezone.utc)
        self.formatted_time = date_time.strftime("%m/%d/%Y, %H:%M:%S")


    def tm(self):
        '''Return the TM'''
        return self.delimitedText(b'<TM>', b'</TM>')
    
    def parse_TM_xml(self)->str:
        xml_txt = self.delimitedText(b'<TM>', b'</TM>')
        return xmltodict.parse(xml_txt)

    def parse_CRC_xml(self)->str:
        '''
        Parse TM XML data from the binary input.

        Returns:
            str: Parsed XML data.

        Raises:
            KeyError: If the start or end text is not found in the input data.
        '''        
        xml_txt = self.delimitedText(b'<CRC>', b'</CRC>')
        return xmltodict.parse(xml_txt)

    def delimitedText(self, startTxt:str, endTxt:str)->str:
        '''
        Extract and decode text delimited by start and end markers from the binary input.

        Args:
            startTxt (str): The start marker for the delimited text.
            endTxt (str): The end marker for the delimited text.

        Returns:
            bytes: Decoded text between the start and end markers.

        Raises:
            ValueError: If the start or end markers are not found in the input data.
        '''
        start = self.data.find(startTxt)
        end = self.data.find(endTxt)
        return self.data[start:end+len(endTxt)].decode()

    def binaryData(self)->bytes:
        '''
        Extracts and returns a segment of binary data based on markers and lengths from the input data.

        Returns:
            bytes: The extracted binary data segment.

        Raises:
            KeyError: If the 'TM' or 'Length' keys are not found in the parsed XML data.
        '''
        tm_xml = self.parse_TM_xml()
        bin_length = int(tm_xml['TM']['Length'])
        bin_start = self.data.find(b'</CRC>\nSTART') + 12
        return self.data[bin_start:bin_start+bin_length]

    def timeStamp(self)->int:
        '''
        Extract the timestamp from the binary data.

        Returns:
            int: The extracted timestamp value.

        Raises:
            struct.error: If there is an issue with unpacking the timestamp from the binary data.
        '''
        return  struct.unpack_from('>L', self.bindata, 0)[0]

class RS41msg(TMmsg):
    # The binary payload for the RS41 contains a couple of
    # metadata fields, followed by multiple data records.
    # The payload is coded as follows:
    # uint32_t start time
    # uint16_t n_samples
    # data records:
    # struct RS41Sample_t {
    #    uint8_t valid;
    #    uint32_t frame;
    #    uint16_t tdry; (tdry+100)*100
    #    uint16_t humidity; (humdity*100)
    #    uint16_t pres; (pres*100)
    #    uint16_t error;
    #};
    def __init__(self, msg_filename:str):
        '''
        Initialize the object with the provided binary data.

        Args:
            msg_filename: The message file name.

        Returns:
            None
        '''
        super().__init__(msg_filename)
        self.records = self.allRS41samples()

    def csvText(self)->list:
        '''
        Generate CSV text lines from the records.

        Returns:
            list: List of CSV text lines.

        Args:
            self: The instance containing records and a CSV header.
        '''

        csv_io = io.StringIO()
        csv_writer = csv.writer(csv_io, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)

        csv_header = ['Instrument:', 'RS41', 'Measurement End Time:', self.formatted_time, 
                   'NCAR RS41 sensor on Strateole 2 Super Pressure Balloons']
        csv_writer.writerow(csv_header)
        
        csv_header = 'valid,unix_time,air_temp_degC,humdity_percent,humidity_sensor_temp,pres_mb,\
            module_error,rs41_rh_percent,wv_mixing_ratio_ppmv'.replace(' ','').split(',')
        csv_writer.writerow(csv_header)

        for r in self.records:
            csv_line = [r['valid'], r['unix_time'], r['air_temp_degC'], r['humdity_percent'],
                        r['humidity_sensor_temp_degC'], r['pres_mb'], r['module_error'],
                        r['rs41_rh_percent'],r['wv_mixing_ratio_ppmv']]
            csv_writer.writerow(csv_line)

        return csv_io.getvalue().split('\r\n')

    def printCsv(self):
        '''
        Prints the CSV text lines generated from the records.

        Args:
            self: The instance containing the CSV text lines to print.
            
        Returns:
            None
        '''

        for r in self.csvText():
            print(r)
    
    def saveCsv(self, out_filename:str)->None:
        with open(out_filename, "w") as out_file:
            for r in self.csvText():
                out_file.write(r)
                out_file.write('\n')

    def decodeRS41sample(self, record)->dict:
        '''
        Decode a binary sample and convert it to real-world values.

        Args:
            record: The binary sample to decode.

        Returns:
            dict: Decoded real-world values of the binary sample.
        '''
        r = {}
        r['valid'] = struct.unpack_from('B', record, 0)[0]
        r['secs_from_start'] = struct.unpack_from('>l', record, 1)[0]
        # print('decodeRS41',self.unix_end_time,r['unix_time'])
        r['air_temp_degC'] = struct.unpack_from('>H', record, 5)[0]/100.0-100.0
        r['humdity_percent'] = struct.unpack_from('>H', record, 7)[0]/100.0
        r['humidity_sensor_temp_degC'] = struct.unpack_from('>H', record, 9)[0]/100.0-100.0
        r['pres_mb'] = struct.unpack_from('>H', record, 11)[0]/50.0
        r['module_error'] = struct.unpack_from('>H', record, 13)[0]
        r['rs41_rh_percent'],r['wv_mixing_ratio_ppmv']=RS41_RH_wvmr(r['air_temp_degC'],r['pres_mb'],r['humdity_percent'],r['humidity_sensor_temp_degC'])
        #print(r)
        return r
    
    def allRS41samples(self)->list:
        '''
        Go through all data samples and convert them to real-world values.

        Returns:
            list: List of dictionaries containing decoded real-world values for each data sample.
        '''
        record_len = 1 + 4 + 2 + 2 + 2 + 2 + 2
        records = []
        for i in range(6, len(self.bindata)-6, record_len):
            record = self.bindata[i:i+record_len]
            records.append(self.decodeRS41sample(record))

        # Compute the unix time for each sample
        start_time = self.unix_end_time - (records[-1]['secs_from_start'] - records[0]['secs_from_start'] + 1)
        for i in range(len(records)):
            records[i]['unix_time'] =  records[i]['secs_from_start'] + start_time

        return records

class LPCmsg(TMmsg):
    def __init__(self, msg_filename:str):
        '''
        Initialize the object with the provided binary data.

        Args:
            msg_filename: The message file name.

        Returns:
            None
        '''
        super().__init__(msg_filename)

        #LPC bins - each number is the left end of the bins in nm.   The first bin has minimal sensitivity
        diams = [275,300,325,350,375,400,450,500,550,600,650,700,750,800,900,1000,1200,1400,1600,1800,2000,2500,3000,3500,4000,6000,8000,10000,13000,16000,24000,24000]
        self.bin_header = list(map(str, diams))

        # Initialize some metadata
        self.sn = 'Unknown'
        self.lat = ''
        self.lon= ''
        self.alt = ''

        tm_xml = self.parse_TM_xml()

        self.instrument = 'Unknown'
        if 'Inst' in tm_xml['TM']:
            self.inst = tm_xml['TM']['Inst']

        if 'StateMess3' in tm_xml['TM']:
            tokens = tm_xml['TM']['StateMess3'].split(',')
            if len(tokens) == 3:
                self.lat = tokens[0]
                self.lon = tokens[1]
                self.alt = tokens[2]
        self.unpackBinary()

    def unpackBinary(self):
        records = int(len(self.bindata)/96) -2
        self.HGBins = np.zeros(shape=(16,records))
        self.LGBins = np.zeros(shape=(16,records))
        self.HKData = np.zeros(shape=(16,records))
        for y in range(records):
          
            self.HKRaw = []
           
            indx = 36 + (y+1)*96
        
            for x in range(16):
                self.HGBins[x,y] = struct.unpack_from('>H',self.bindata,indx + x*2)[0]    
                self.LGBins[x,y] = struct.unpack_from('>H',self.bindata,indx + x*2 + 32)[0]
                self.HKRaw.append(struct.unpack_from('>H',self.bindata,indx + x*2 + 64)[0])
            

            #modified to agree with the current LPC HK scheme - this will need to be updated for mission 
            self.HKData[0,y] = self.HKRaw[0] + self.unix_end_time #elapsed time since the start of the measurement in seconds
            self.HKData[1,y] = self.HKRaw[1]  # Pump1 Current in mA
            self.HKData[2,y] = self.HKRaw[2]  # Pump2 Current in mA
            self.HKData[3,y] = self.HKRaw[3]  # Detector Current in mA
            
            self.HKData[4,y] = self.HKRaw[4] / 1000.0 # Detector voltage in V
            self.HKData[5,y] = self.HKRaw[5] / 1000.0 # PHA Voltage in volts
            self.HKData[6,y] = self.HKRaw[6] / 1000.0 # Tennsy V in volts
            self.HKData[7,y] = self.HKRaw[7] / 1000.0 # VBattery V 
            self.HKData[8,y] = self.HKRaw[8] / 1000.0 # Flow in LPM
            self.HKData[9,y] = self.HKRaw[9] # Pump1 PWM drive signal (0 - 1023)
            self.HKData[10,y] = self.HKRaw[10] # Pump2 PWM drive signal (0 - 1023)
            self.HKData[11,y] = self.HKRaw[11] / 100.0 - 273.15 # Pump1 T in C
            self.HKData[12,y] = self.HKRaw[12] / 100.0 - 273.15 # Pump2 T in C
            self.HKData[13,y] = self.HKRaw[13] / 100.0 - 273.15 # Laser T in C
            self.HKData[14,y] = self.HKRaw[14] / 100.0 - 273.15 # Board T in C
            self.HKData[15,y] = self.HKRaw[15] / 100.0 - 273.15 # Inlet T in C
    def csvText(self)->list:
        '''
        Generate CSV text lines from the records.

        Returns:
            list: List of CSV text lines.

        Args:
            self: The instance containing records and a CSV header.
        '''

        csv_io = io.StringIO()
        csv_writer = csv.writer(csv_io, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)

        #LPC bins - each number is the left end of the bins in nm.   The first bin has minimal sensitivity
        diams = [275,300,325,350,375,400,450,500,550,600,650,700,750,800,900,1000,1200,1400,1600,1800,2000,2500,3000,3500,4000,6000,8000,10000,13000,16000,24000,24000]
        bin_header = list(map(str,diams))

        csv_writer = csv.writer(csv_io, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        
        header1 = ['Instrument:', self.inst, 'Measurement End Time:', self.formatted_time, 
                   'LASP Optical Particle Counter on Strateole 2 Super Pressure Balloons']
        csv_writer.writerow(header1)

        header2 = ['GPS Position at start of Measurement ', 'Latitude: ', self.lat, 'Longitude: ', self.lon, 
                   'Altitude [m]:',self.alt]
        csv_writer.writerow(header2)

        header3 = ['Time', 'Pump1_I','Pump2_I','PHA_I', 'PHA_12V','PHA_3V3','CPU_V', 'Input_V', 'Flow', 
                   'Pump1_PWM', 'Pump2_PWM','Pump1_T', 'Pump2_T', 'Laser_T', 'PCB_T', 'Inlet_T'] + bin_header
        csv_writer.writerow(header3)

        header4 = ['[unix_time]', '[mA]','[mA]','[mA]','[V]','[V]','[V]', '[V]', '[SLPM]','[#]','[#]', '[C]', '[C]','[C]', '[C]', '[C]'] + ['[diam >nm]']*len(bin_header)
        csv_writer.writerow(header4)

        for row in range(len(self.HKData[0,:])):
            csv_writer.writerow(self.HKData[:,row].tolist() + self.HGBins[:,row].tolist() +self.LGBins[:,row].tolist())

        return csv_io.getvalue().split('\r\n')

    def printCsv(self):
        '''
        Prints the CSV text lines generated from the records.

        Args:
            self: The instance containing the CSV text lines to print.
            
        Returns:
            None
        '''
        for r in self.csvText():
            print(r)
    
    def saveCsv(self, out_filename:str)->None:
        with open(out_filename, "w") as out_file:
            for r in self.csvText():
                out_file.write(r)
                out_file.write('\n')

def argParse():
    '''
    Parse command line arguments for the TMdecoder script.

    Returns:
        Parsed command line arguments.

    '''
    parser = argparse.ArgumentParser(
                        prog='TMdecoder',
                        description='Decode a LASP StratoCore TM message and produce CSV',
                        epilog='''
                        If -l or -r are not specified, try to automatically determine the msg type.
                        Only one of -c or -b is allowed. In batch mode, the current directory
                        is searched for the files.'
                        ''')
    parser.add_argument('filename', help='TM message file, or file extension (for batch processing)')
    parser.add_argument('-l', '--lpc', action='store_true', help='LPC file')
    parser.add_argument('-r', '--rs41', action='store_true', help='RS41 file')           
    parser.add_argument('-c', '--csv', help='Save CSV to a file')
    parser.add_argument('-b', '--batch', action='store_true', help='Batch process, creating .csv files')
    parser.add_argument('-t', '--tm', action='store_true', help='Print the TM header')
    parser.add_argument('-q', '--quiet',  action='store_true', help='Turn off printing')  # on/off flag

    args=parser.parse_args()

    if args.lpc & args.rs41:
        print('Only one of -l or -r can be specified')
        parser.print_usage()
        sys.exit(1)

    if (args.csv != None) & args.batch:
        print('Only one of -c or -b can be specified')
        parser.print_usage()
        sys.exit(1)

    args.msg_type = None
    if args.lpc:
        args.msg_type = 'lpc'
    if args.rs41:
        args.msg_type = 'rs41'

    args.filename_or_ext = args.filename

    return args

def determine_msg_type(filename:str)->str:
    msg_type = 'lpc'

    msg = TMmsg(filename)
    tm = msg.parse_TM_xml()
    if 'StateMess2' in tm['TM'] and tm['TM']['StateMess2'] == 'RS41':
        msg_type = 'rs41'

    return msg_type

def get_files(ext:str):
    tm_files = glob.glob(f'*{ext}')
    csv_files = [f.replace(ext, '.csv') for f in tm_files]
    return tm_files, csv_files

if __name__ == "__main__":

    args = argParse()

    if args.batch:
        tm_files, csv_files = get_files(args.filename_or_ext)
    else:
        tm_files = [args.filename_or_ext]
        csv_files = [args.csv]

    for tm_file, csv_file in zip(tm_files, csv_files):

        try:
            if args.msg_type:
                msg_type = args.msg_type
            else:
                msg_type = determine_msg_type(tm_file)

            if msg_type == 'lpc':
                msg = LPCmsg(tm_file)
            if msg_type == 'rs41':
                msg = RS41msg(tm_file)

            if args.tm:
                print(msg.tm())

            if not args.quiet:
                msg.printCsv()

            if csv_file:
                msg.saveCsv(csv_file)
        except struct.error:
            print(f'*** Error decoding binary data in {tm_file}, file was not processed')
