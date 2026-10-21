from app.analysis import analyze_candles

def make_candles(count=220,rising=True):
    rows=[]; price=100.0
    for i in range(count):
        price += 0.15 if rising else -0.15
        rows.append({'time':float(i),'open':price-0.04 if rising else price+0.04,'high':price+0.2,'low':price-0.2,'close':price,'volume':10.0})
    return rows

def test_analysis_has_expected_fields():
    result=analyze_candles(make_candles(),'TESTUSDT','5m')
    for key in ('signal','confidence','entry','tp1','tp2','tp3','sl','reasons'): assert key in result
    assert result['signal'] in {'BUY','SELL','WAIT'}
    assert 50 <= result['confidence'] <= 90

def test_analysis_rejects_short_history():
    try: analyze_candles(make_candles(20),'TESTUSDT','5m')
    except ValueError as exc: assert '35 candles' in str(exc)
    else: raise AssertionError('Expected ValueError')
