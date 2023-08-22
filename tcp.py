import asyncio
from os import urandom
from sys import byteorder
from tcputils import *

class Servidor:
    def __init__(self, rede, porta):
        self.rede = rede
        self.porta = porta
        self.conexoes = {}
        self.callback = None
        self.rede.registrar_recebedor(self._rdt_rcv)

    def registrar_monitor_de_conexoes_aceitas(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que uma nova conexão for aceita
        """
        self.callback = callback

    def _rdt_rcv(self, src_addr, dst_addr, segment):
        src_port, dst_port, seq_no, ack_no, \
            flags, window_size, checksum, urg_ptr = read_header(segment)

        if dst_port != self.porta:
            # Ignora segmentos que não são destinados à porta do nosso servidor
            return
        if not self.rede.ignore_checksum and calc_checksum(segment, src_addr, dst_addr) != 0:
            print('descartando segmento com checksum incorreto')
            return

        payload = segment[4*(flags>>12):]
        id_conexao = (src_addr, src_port, dst_addr, dst_port)

        if (flags & FLAGS_SYN) == FLAGS_SYN:
            # A flag SYN estar setada significa que é um cliente tentando estabelecer uma conexão nova
            # TODO: talvez você precise passar mais coisas para o construtor de conexão
            # TODO: você precisa fazer o handshake aceitando a conexão. Escolha se você acha melhor
            # fazer aqui mesmo ou dentro da classe Conexao.
            conexao = self.conexoes[id_conexao] = Conexao(self, id_conexao, seq_no, ack_no + 1)
            seqnorand=int.from_bytes(urandom(4), byteorder)
            ack_no += seq_no + 1
            segmentohandshake = make_header(dst_port, src_port, seqnorand, ack_no, FLAGS_SYN | FLAGS_ACK)
            segmentohandshake = fix_checksum(segmentohandshake, dst_addr, src_addr)
            self.rede.enviar(segmentohandshake, src_addr)
            if self.callback:
                self.callback(conexao)
        elif id_conexao in self.conexoes:
            # Passa para a conexão adequada se ela já estiver estabelecida
            self.conexoes[id_conexao]._rdt_rcv(seq_no, ack_no, flags, payload)
        else:
            print('%s:%d -> %s:%d (pacote associado a conexão desconhecida)' %
                  (src_addr, src_port, dst_addr, dst_port))


class Conexao:
    def __init__(self, servidor, id_conexao, sequencianova, sequenciaanterior):
        self.servidor = servidor
        self.id_conexao = id_conexao
        self.callback = None
        self.sequencianova = sequencianova + 1
        self.sendbase = sequencianova
        self.sequenciaanterior = sequenciaanterior
        self.conexaoaberta = True
        self.timerligado = False
        self.buff = []
        self.timer = None  # um timer pode ser criado assim; esta linha é só um exemplo e pode ser removida
        #self.timer.cancel()   # é possível cancelar o timer chamando esse método; esta linha é só um exemplo e pode ser removida

    def _exemplo_timer(self):
        # Esta função é só um exemplo e pode ser removida
        self.timer = None
        dados = self.buff.pop(0)
        self.buff.insert(0, dados)
        self.servidor.rede.enviar(dados, self.id_conexao[2])
        if self.timer:
            self.timer.cancel()
            self.timer = None
            self.timerligado = False
        self.timer = asyncio.get_event_loop().call_later(1, self._exemplo_timer)
        #self.timerligado = True
        print('Este é um exemplo de como fazer um timer')

    def _rdt_rcv(self, seq_no, ack_no, flags, payload):
        # TODO: trate aqui o recebimento de segmentos provenientes da camada de rede.
        # Chame self.callback(self, dados) para passar dados para a camada de aplicação após
        # garantir que eles não sejam duplicados e que tenham sido recebidos em ordem.
        if (self.conexaoaberta):
            if (seq_no == self.sequencianova and payload):
                if (seq_no > self.sendbase) and ((flags & FLAGS_ACK) == FLAGS_ACK):
                    if len(self.buff) > 0:
                        self.buff.pop(0)
                        if len(self.buff) == 0:
                            if self.timer:
                                self.timer.cancel()
                                self.timer = None
                                self.timerligado = False
                        else:
                            if self.timer:
                                self.timer.cancel()
                                self.timer = None
                                self.timerligado = False
                            self.timer = asyncio.get_event_loop().call_later(1, self._exemplo_timer)

                self.timer = None
                self.callback(self, payload)
                self.sequencianova = self.sequencianova + len(payload)
                self.sequenciaanterior = ack_no
                if len(payload) > 0:
                    segmentoconfirma = make_header(self.id_conexao[1], self.id_conexao[3], ack_no, self.sequencianova, FLAGS_ACK)
                    segmentoconfirma = fix_checksum(segmentoconfirma, self.id_conexao[0], self.id_conexao[2])
                    self.servidor.rede.enviar(segmentoconfirma, self.id_conexao[2])
            else:
                if (seq_no > self.sendbase) and ((flags & FLAGS_ACK) == FLAGS_ACK):
                    if len(self.buff) > 0:
                        self.buff.pop(0)
                        if len(self.buff) == 0:
                            print("timer cancelado")
                            if self.timer:
                                self.timer.cancel()
                                self.timer = None
                                self.timerligado = False
                        else:
                            if self.timer:
                                self.timer.cancel()
                                self.timer = None
                                self.timerligado = False
                            self.timer = asyncio.get_event_loop().call_later(1, self._exemplo_timer)
                self.sequenciaanterior = ack_no
            #print('recebido payload: %r' % payload)

            if (flags & FLAGS_FIN) == FLAGS_FIN:
                self.callback(self, b'')
                self.sequencianova = self.sequencianova + 1
                self.sequenciaanterior = ack_no
                finack = make_header(self.id_conexao[1], self.id_conexao[3], self.sequenciaanterior, self.sequencianova, FLAGS_ACK)
                finack = fix_checksum(finack, self.id_conexao[0], self.id_conexao[2]) 
                self.servidor.rede.enviar(finack, self.id_conexao[2])
                self.conexaoaberta = False

    # Os métodos abaixo fazem parte da API
    
    def registrar_recebedor(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que dados forem corretamente recebidos
        """
        self.callback = callback

    def enviar(self, dados):
        """
        Usado pela camada de aplicação para enviar dados
        """
        # TODO: implemente aqui o envio de dados.
        # Chame self.servidor.rede.enviar(segmento, dest_addr) para enviar o segmento
        # que você construir para a camada de rede.
        payloadbuff = b''
        segsender = make_header(self.id_conexao[1], self.id_conexao[3], self.sequenciaanterior, self.sequencianova, FLAGS_ACK)
        if len(dados) <= MSS:
            dados = segsender + dados
        else:
            payloadbuff = dados[MSS:]
            dados = segsender + dados[:MSS]

        dados = fix_checksum(dados, self.id_conexao[0], self.id_conexao[2])
        self.servidor.rede.enviar(dados, self.id_conexao[2])
        self.buff.append(dados)
        
        self.sequenciaanterior += len(dados) - 20

        if not self.timerligado:
            if self.timer:
                self.timer.cancel()
                self.timer = None
                self.timerligado = False
            self.timer = asyncio.get_event_loop().call_later(1, self._exemplo_timer)

        if len(payloadbuff) != 0:
            self.enviar(payloadbuff)

    def fechar(self):
        """
        Usado pela camada de aplicação para fechar a conexão
        """
        # TODO: implemente aqui o fechamento de conexão
        finseg = make_header(self.id_conexao[1], self.id_conexao[3], self.sequenciaanterior, self.sequencianova, FLAGS_ACK | FLAGS_FIN)
        finseg = fix_checksum(finseg, self.id_conexao[0], self.id_conexao[2])
        self.servidor.rede.enviar(finseg, self.id_conexao[2])
