import json
import struct
import tempfile
import unittest
from pathlib import Path
from ps1kit.drive import *
from ps1kit.runner import export, run

class DriveTests(unittest.TestCase):
    def setUp(self):
        self.d=VirtualDrive(fixtures()['mixed'])
    def error(self,code,fn):
        with self.assertRaises(DriveError) as ctx: fn()
        self.assertEqual(ctx.exception.code,code)
    def test_header_and_form1(self):
        raw=self.d.read_raw(0)
        self.assertEqual(len(raw),2352)
        self.assertEqual(raw[:16],b'\x00'+b'\xff'*10+b'\x00\x00\x02\x00\x02')
        self.assertEqual(raw[16:24],bytes([1,0,8,0])*2)
        self.assertEqual(self.d.read_user(0),raw[24:2072])
    def test_modes(self):
        d=VirtualDrive(fixtures()['forms'])
        self.assertEqual(d.read_raw(0)[15],1)
        self.assertEqual(len(d.read_user(0)),2048)
        self.assertEqual(d.read_raw(10)[18]&0x20,0x20)
        self.assertEqual(len(d.read_user(10)),2324)
    def test_bcd_carry(self):
        disc=Disc('long',(Track(1,0,5000,'MODE1'),))
        self.assertEqual(sector(disc,4350)[12:15],b'\x01\x00\x00')
    def test_audio_samples(self):
        raw=self.d.read_raw(24)
        samples=struct.unpack('<1176h',raw)
        self.assertEqual(samples[::2],samples[1::2])
        self.assertGreater(max(samples),10000)
        self.assertLess(min(samples),-10000)
        self.error('not_data',lambda:self.d.read_user(24))
    def test_boundary_and_leadout(self):
        self.assertEqual(len(self.d.read_raw(23,2)),4704)
        self.assertEqual(self.d.toc().leadout,40)
        self.error('out_of_range',lambda:self.d.read_raw(39,2))
        self.error('out_of_range',lambda:self.d.read_raw(40))
    def test_invalid_requests(self):
        for lba,count in [(-1,1),(0,0),(0,-1),(0,4097),(1.2,1),(True,1)]:
            self.error('invalid_request',lambda:self.d.read_raw(lba,count))
    def test_invalid_layouts(self):
        for tracks in [(),(Track(1,1,2,'AUDIO'),),
                       (Track(1,0,2,'AUDIO'),Track(2,3,5,'AUDIO')),
                       (Track(2,0,2,'AUDIO'),)]:
            with self.assertRaises(ValueError): Disc('bad',tracks)
    def test_swap_and_empty(self):
        first=self.d.read_raw(0); gen=self.d.state().generation
        self.error('tray_closed',lambda:self.d.insert(fixtures()['disc2']))
        self.d.eject()
        self.error('no_medium',lambda:self.d.toc())
        self.error('no_medium',lambda:self.d.read_raw(0))
        self.d.insert(fixtures()['disc2'])
        self.assertEqual(self.d.state().generation,gen+2)
        self.assertNotEqual(first,self.d.read_raw(0))
    def test_retries(self):
        self.d.failures[1]=2
        self.assertEqual(len(read_retry(self.d,1)),2352)
        self.assertEqual(self.d.failures[1],0)
        self.d.failures[2]=-1
        self.error('read_failure',lambda:read_retry(self.d,2))
        with self.assertRaises(ValueError): read_retry(self.d,0,0)
    def test_disconnect_mid_batch(self):
        self.d.disconnect_at=24
        self.error('disconnected',lambda:self.d.read_raw(23,2))
        self.error('disconnected',lambda:read_retry(self.d,0))
        self.d.disconnect_at=None; self.d.reconnect()
        self.assertEqual(len(self.d.read_raw(24)),2352)
    def test_short_read_adapter(self):
        class Short(VirtualDrive):
            def read_raw(self,lba,count=1): return b''
        self.error('short_read',lambda:read_retry(Short(fixtures()['data']),0))
    def test_media_changes_during_read(self):
        class Changed(VirtualDrive):
            def read_raw(self,lba,count=1):
                data=super().read_raw(lba,count)
                self.generation+=1
                return data
        self.error('medium_changed',lambda:read_retry(Changed(fixtures()['data']),0))
    def test_media_changes_after_error(self):
        class Changed(VirtualDrive):
            def read_raw(self,lba,count=1):
                self.generation+=1
                raise DriveError('read_failure',lba)
        self.error('medium_changed',lambda:read_retry(Changed(fixtures()['data']),0))
    def test_exports(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            export(Path(tmp)); p=Path(tmp)
            self.assertEqual((p/'mixed.bin').stat().st_size,40*2352)
            self.assertIn('TRACK 02 AUDIO\n    INDEX 01 00:00:24',(p/'mixed.cue').read_text())
            self.assertEqual((p/'multi.m3u').read_text().splitlines(),['data.cue','disc2.cue'])
            raw=(p/'data.bin').read_bytes()
            export(p)
            self.assertEqual(raw,(p/'data.bin').read_bytes())
    def test_report(self):
        r=json.loads(json.dumps(run()))
        self.assertEqual(r['summary'],{'passed':9,'failed':0})
        for key in ['hardware_tested','games_tested','swanstation_integration_tested']:
            self.assertIs(r[key],False)
        self.assertEqual(len({c['id'] for c in r['cases']}),9)

if __name__=='__main__': unittest.main(verbosity=2)

