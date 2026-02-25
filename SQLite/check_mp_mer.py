#!/usr/bin/env python3
"""
Quick diagnostic - Check MERKEY values in MP_MER.FPB
"""

import struct

fpb_path = r'D:\Projects\CatalogAutomation\Data\MP_MER.FPB'

class FoxProDBF:
    def __init__(self, filename):
        self.filename = filename
        self.file = open(filename, 'rb')
        self._read_header()
        self._read_field_descriptors()
    
    def _read_header(self):
        header = self.file.read(32)
        self.num_records = struct.unpack('<I', header[4:8])[0]
        self.header_length = struct.unpack('<H', header[8:10])[0]
        self.record_length = struct.unpack('<H', header[10:12])[0]
    
    def _read_field_descriptors(self):
        self.fields = []
        self.file.seek(32)
        
        while True:
            field_data = self.file.read(32)
            if field_data[0] == 0x0D:
                break
            
            name = field_data[0:11].decode('latin1').strip('\x00').strip()
            field_type = chr(field_data[11])
            length = field_data[16]
            
            self.fields.append({
                'name': name,
                'type': field_type,
                'length': length
            })
        
        self.data_start = self.file.tell()
    
    def __iter__(self):
        self.file.seek(self.data_start)
        
        for _ in range(self.num_records):
            record_data = self.file.read(self.record_length)
            
            if record_data[0] == 0x2A:  # Deleted
                continue
            
            record = {}
            offset = 1
            
            for field in self.fields:
                field_data = record_data[offset:offset + field['length']]
                value = field_data.decode('latin1', errors='ignore').strip()
                record[field['name']] = value if value else None
                offset += field['length']
            
            yield record
    
    def close(self):
        self.file.close()

print("=" * 80)
print(f"Checking: {fpb_path}")
print("=" * 80)

try:
    dbf = FoxProDBF(fpb_path)
    
    print(f"Total records: {dbf.num_records:,}")
    print()
    print("First 20 MERKEY values:")
    print("-" * 80)
    
    count = 0
    merkey_set = set()
    
    for row in dbf:
        if count < 20:
            merkey = row.get('MERKEY')
            medesc = row.get('MEDESC', '')
            print(f"{count+1:3}. MERKEY: '{merkey}' | DESC: {medesc[:40]}")
            count += 1
        
        # Collect unique MERKEYs
        merkey = row.get('MERKEY')
        if merkey:
            merkey_set.add(merkey)
        
        if count >= 1000:  # Check first 1000 records
            break
    
    print()
    print(f"Unique MERKEYs in first 1000 records: {len(merkey_set)}")
    print("Sample unique MERKEYs:")
    for mk in sorted(list(merkey_set))[:20]:
        print(f"  '{mk}'")
    
    dbf.close()
    
except FileNotFoundError:
    print(f"ERROR: File not found at {fpb_path}")
    print("Please check the path or copy the file to D:\\Projects\\CatalogAutomation\\Data\\")
except Exception as e:
    print(f"ERROR: {e}")

print("=" * 80)
