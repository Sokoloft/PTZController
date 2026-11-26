"""Optional Qt mouse/wheel regression; use QT_QPA_PLATFORM=offscreen in CI."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import json, tempfile, threading
from werkzeug.serving import make_server
from ptz_controller.app import create_app
from ptz_controller.engine import Engine
from ptz_controller.cli import RequestHandler
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QEventLoop, QTimer, QUrl, QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
app=QApplication(['ptz-controls-check'])
tmp=tempfile.TemporaryDirectory();engine=Engine(Path(tmp.name)/'config.json')
server=make_server('127.0.0.1',0,create_app(engine),threaded=True,request_handler=RequestHandler)
http=threading.Thread(target=server.serve_forever,daemon=True);http.start()
worker=threading.Thread(target=engine.run,daemon=True);worker.start()
errors=[]
class Page(QWebEnginePage):
    def javaScriptConsoleMessage(self,level,message,line,source):
        if level==QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:errors.append(message)
view=QWebEngineView();view.setPage(Page(view));view.resize(1200,900);view.show()
def js(script):
    result=[];loop=QEventLoop()
    view.page().runJavaScript(script,lambda value:(result.append(value),loop.quit()))
    QTimer.singleShot(3000,loop.quit);loop.exec()
    assert result, 'JavaScript timed out'
    return result[0]
try:
    loaded=QEventLoop();view.loadFinished.connect(loaded.quit)
    view.load(QUrl(f'http://127.0.0.1:{server.server_port}'))
    QTimer.singleShot(5000,loaded.quit);loaded.exec();QTest.qWait(500)
    assert js("document.querySelectorAll('.select-trigger').length===document.querySelectorAll('select').length")
    js("document.getElementById('settings').open=true;document.getElementById('command').closest('label').querySelector('.select-trigger').scrollIntoView({block:'center'});")
    QTest.qWait(150)
    rect=json.loads(js("JSON.stringify((()=>{const r=document.getElementById('command').closest('label').querySelector('.select-trigger').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})())"))
    target=view.focusProxy() or view;point=QPoint(round(rect['x']),round(rect['y']))
    QTest.mouseClick(target,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
    QTest.qWait(1100)
    assert js("document.querySelector('.select-menu:not([hidden])')!==null"), 'single-click menu did not stay open'
    rect=json.loads(js("JSON.stringify((()=>{const r=document.querySelector('.select-menu:not([hidden])').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})())"))
    pos=QPoint(round(rect['x']),round(rect['y']));global_pos=target.mapToGlobal(pos)
    event=QWheelEvent(QPointF(pos),QPointF(global_pos),QPoint(),QPoint(0,-360),Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
    QTest.mouseMove(target,pos);QTest.qWait(50);js("window.wheelSeen=[];document.addEventListener('wheel',e=>wheelSeen.push({delta:e.deltaY,target:e.target.className}),true)");app.sendEvent(target,event);QTest.qWait(200);print('WHEEL',js("JSON.stringify({seen:window.wheelSeen,top:document.querySelector('.select-menu:not([hidden])').scrollTop,height:document.querySelector('.select-menu:not([hidden])').clientHeight,full:document.querySelector('.select-menu:not([hidden])').scrollHeight})"),flush=True)
    assert js("document.querySelector('.select-menu:not([hidden])').scrollTop>0"), 'wheel did not scroll menu'
    view.grab().save('/tmp/ptz-dropdown-ui.png')
    QTest.keyClick(target,Qt.Key.Key_Escape);QTest.qWait(50)
    assert js("document.querySelector('.select-menu:not([hidden])')===null")
    old=js("document.getElementById('command').value")
    QTest.mouseClick(target,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier,point)
    QTest.keyClick(target,Qt.Key.Key_Down);QTest.keyClick(target,Qt.Key.Key_Return);QTest.qWait(100)
    assert js("document.getElementById('command').value")!=old
    assert js("document.querySelector('.select-menu:not([hidden])')===null")
    assert not errors,errors
    print('PASS: real Qt mouse click stays open through polling, wheel scrolls, Escape closes, keyboard selects; no JavaScript errors')
    print('Detected face labels:',[(d['name'],d['button_labels']) for d in engine.snapshot()['devices']])
finally:
    engine.quitting.set();worker.join(3);server.shutdown();server.server_close();view.close();tmp.cleanup()
