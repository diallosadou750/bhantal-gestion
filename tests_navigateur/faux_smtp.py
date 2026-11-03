"""Faux serveur d'e-mail local : reçoit les messages sans rien envoyer sur Internet (pour tester les rappels)."""
import socketserver
import threading


class FauxSMTP:
    def __init__(self):
        self.messages = []; outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda s: (self.wfile.write((s + "\r\n").encode()), self.wfile.flush())
                w("220 faux.local ESMTP"); mail = {"to": []}
                while True:
                    line = self.rfile.readline().decode("utf-8", "replace").rstrip("\r\n")
                    if not line: return
                    cmd = line.split(" ", 1)[0].upper()
                    if cmd == "RCPT": mail["to"].append(line.split(":", 1)[1].strip(" <>")); w("250 ok")
                    elif cmd == "DATA":
                        w("354 go"); data = []
                        while True:
                            l = self.rfile.readline().decode("utf-8", "replace")
                            if l.rstrip("\r\n") == ".": break
                            data.append(l)
                        outer.messages.append({"to": list(mail["to"]), "raw": "".join(data)}); mail = {"to": []}; w("250 queued")
                    elif cmd == "QUIT": w("221 bye"); return
                    else: w("250 ok")
        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H); self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown(); self.srv.server_close()
