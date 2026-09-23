"""Controlled GRIB metadata/value attacks; no weather download or real output."""
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from wind_forecast.weather.gfs_extract import _decode_message, _read_range


class DecoderGuardTests(unittest.TestCase):
    def test_wrong_run_centre_grid_and_finite_missing_are_rejected(self):
        run=datetime(2026,1,1,tzinfo=timezone.utc);valid=run.replace(hour=6)
        base={'shortName':'10u','typeOfLevel':'heightAboveGround','level':10,'validityDate':20260101,'validityTime':600,
              'edition':2,'centre':'kwbc','discipline':0,'stepType':'instant','dataDate':20260101,'dataTime':0,
              'gridType':'regular_ll','iDirectionIncrementInDegrees':.25,'jDirectionIncrementInDegrees':.25,
              'parameterCategory':2,'parameterNumber':2,'missingValue':9999.}
        for change,value in [({},4.),({'centre':'ecmf'},4.),({'dataTime':1200},4.),({'gridType':'reduced_gg'},4.),({},9999.),({},151.)]:
            metadata={**base,**change}
            fake=SimpleNamespace(codes_new_from_message=lambda p:1,codes_get=lambda h,k:metadata[k],
                codes_release=lambda h:None,codes_grib_find_nearest=lambda *a:[{'value':value,'lat':43.75,'lon':78.5,'distance':12}])
            with self.subTest(change=change,value=value),patch.dict(sys.modules,{'eccodes':fake}):
                if not change and value==4.:
                    self.assertEqual(_decode_message(b'test','u_10m_ms',valid,{'T1':(43.6,78.5)},run)['T1']['value'],4.)
                else:
                    with self.assertRaises(ValueError):_decode_message(b'test','u_10m_ms',valid,{'T1':(43.6,78.5)},run)

    def test_range_requires_version_evidence_before_network(self):
        with patch('wind_forecast.weather.gfs_extract.urlopen',side_effect=AssertionError('network forbidden')):
            with self.assertRaisesRegex(ValueError,'Version-bound'):_read_range('https://example.invalid',0,20)

    def test_downloaded_range_must_match_probe_version(self):
        when=datetime(2026,1,1,tzinfo=timezone.utc)
        evidence=SimpleNamespace(etag='"original"',size=21,last_modified=when)
        class Response:
            status=206
            headers={'ETag':'"changed"','Content-Range':'bytes 0-20/21','Last-Modified':'Thu, 01 Jan 2026 00:00:00 GMT'}
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,count):return b'0'*21
        with patch('wind_forecast.weather.gfs_extract.urlopen',return_value=Response()):
            with self.assertRaisesRegex(ValueError,'version'):_read_range('https://example.invalid',0,20,evidence=evidence)
