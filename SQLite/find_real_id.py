#!/usr/bin/env python3
"""
Show ALL fields from MP_MER.FPB to find the real product identifier
"""

import struct

fpb_path = r'F:\SSIMS\MP_MER.FPB'

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
    
    def read_record(self, index):
        self.file.seek(self.data_start + (index * self.record_length))
        record_data = self.file.read(self.record_length)
        
        if record_data[0] == 0x2A:  # Deleted
            return None
        
        record = {}
        offset = 1
        
        for field in self.fields:
            field_data = record_data[offset:offset + field['length']]
            value = field_data.decode('latin1', errors='ignore').strip()
            record[field['name']] = value if value else None
            offset += field['length']
        
        return record

print("=" * 80)
print("ALL FIELDS FROM FIRST RECORD - LOOKING FOR REAL PRODUCT ID")
print("=" * 80)

try:
    dbf = FoxProDBF(fpb_path)
    
    # Read first non-deleted record
    record = None
    for i in range(100):
        record = dbf.read_record(i)
        if record:
            break
    
    if record:
        print("\nComparing with database MERKEY: 1000016")
        print("Looking for field that contains: 1000016")
        print("-" * 80)
        
        for field in dbf.fields[:30]:  # Show first 30 fields
            value = record.get(field['name'], '')
            
            # Highlight if value might be a product ID
            marker = ""
            if value and len(value) >= 6 and value.isdigit():
                marker = " ← POSSIBLE ID!"
            if value and '1000016' in value:
                marker = " ← ★ MATCHES DATABASE!"
            if value and '1019919' in value:
                marker = " ← ★ MATCHES DATABASE!"
            
            print(f"{field['name']:12} = '{value}'{marker}")
    
    print()
    print("=" * 80)
    print("Now checking a few more records to find pattern...")
    print("=" * 80)
    
    # Check records 0, 10, 20 to see patterns
    for idx in [0, 10, 20]:
        record = dbf.read_record(idx)
        if record:
            print(f"\nRecord {idx}:")
            # Show key fields
            for fname in ['OLDEAN', 'MERKEY', 'MEANCS', 'MEANBX', 'MEAN13', 'MEDESC', 'MEMSEQ']:
                val = record.get(fname, '')
                print(f"  {fname:12} = '{val}'")
    
    dbf.close()
    
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
