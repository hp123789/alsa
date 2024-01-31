import numpy as np
import pyaudio
import signal
import logging
import sys
import argparse
from redis import Redis

class StandaloneBrandNode:
    """
    The life cycle of a standalone BRAND node is not controlled by the supergraph, so all the parameters shoud be defined in the node itself. The only connection with the node and BRAND system is that the node can read and write to Redis.
    """

    def __init__(self):

        # parse input arguments
        argp = argparse.ArgumentParser()
        argp.add_argument('-n', '--nickname', type=str, required=True, default='default_standalone_node')
        argp.add_argument('-i', '--redis_host', type=str, required=True, default='localhost')
        argp.add_argument('-p', '--redis_port', type=int, required=False, default=6379)
        argp.add_argument('-s', '--redis_socket', type=str, required=False)
        argp.add_argument('-l', '--log_level', type=str, required=False, default='INFO')
        args = argp.parse_args()

        len_args = len(vars(args))
        if(len_args < 3):
            print("Arguments passed: {}".format(len_args))
            print("Please check the arguments passed")
            sys.exit(1)

        self.NAME = args.nickname
        redis_host = args.redis_host
        redis_port = args.redis_port
        redis_socket = args.redis_socket
        log_level = args.log_level

        # connect to Redis
        self.r = self.connectToRedis(redis_host, redis_port, redis_socket)

        # set up logging
        numeric_level = getattr(logging, log_level.upper(), None)

        if not isinstance(numeric_level, int):
            raise ValueError('Invalid log level: %s' % log_level)

        logging.basicConfig(format=f'%(asctime)s [{self.NAME}] %(levelname)s: %(message)s',
                            level=numeric_level)

        signal.signal(signal.SIGINT, self.terminate)

    def connectToRedis(self, redis_host, redis_port, redis_socket=None):
        """
        Establish connection to Redis and post initialized status to respective Redis stream
        If we supply a -h flag that starts with a number, then we require a -p for the port
        If we fail to connect, then exit status 1
        # If this function completes successfully then it executes the following Redis command:
        # XADD nickname_state * code 0 status "initialized"        
        """

        #redis_connection_parse = argparse.ArgumentParser()
        #redis_connection_parse.add_argument('-i', '--redis_host', type=str, required=True, default='localhost')
        #redis_connection_parse.add_argument('-p', '--redis_port', type=int, required=True, default=6379)
        #redis_connection_parse.add_argument('-n', '--nickname', type=str, required=True, default='redis_v0.1')

        #args = redis_connection_parse.parse_args()
        #len_args = len(vars(args))
        #print("Redis arguments passed:{}".format(len_args))

        try:
            if redis_socket:
                r = Redis(unix_socket_path=redis_socket)
                print(f"[{self.NAME}] Redis connection established on socket:"
                      f" {redis_socket}")
            else:
                r = Redis(redis_host, redis_port, retry_on_timeout=True)
                print(f"[{self.NAME}] Redis connection established on host:"
                      f" {redis_host}, port: {redis_port}")
        except Exception as e:
            print(f"[{self.NAME}] Error with Redis connection, check again: {e}")
            sys.exit(1)

        return r
    
    def run(self):
        while True:
            self.work()
    
    def work(self):
        pass
    
    def terminate(self, sig, frame):
        # TODO: log the termination state to Redis?
        logging.info('SIGINT received, Exiting')
        self.r.close()
        self.cleanup()
        #self.sock.close()
        sys.exit(0)

    def cleanup(self):
        # Does whatever cleanup is required for when a SIGINT is caught
        # When this function is done, it wriest the following:
        #     XADD nickname_state * code 0 status "done"
        pass


class AudioPlayer(StandaloneBrandNode):
    def __init__(self):
        super().__init__()

        # Initialize parameters
        self.parameter_initialization()

        self.chunk_size = int(self.audio_fs * self.interval) # audio buffer size
        self.redis_timeout_ms = self.default_redis_timeout_ms
        self.task_state = self.task_state_default

        # Initialise audio player
        self.initialise_audio_player()

        # Store entry number seen here such that it can be accessed by other functions
        self.last_entry_seen = "$"
        self.redis_connected = True

        # terminate on SIGINT
        signal.signal(signal.SIGINT, self.terminate)
    
    def parameter_initialization(self):
        self.input_stream              = 'pred_audio'
        self.task_state_default        = -1
        self.audio_fs                  = 16000
        self.interval                  = 0.01
        self.audio_buffer_scaler       = 1
        self.norm_factor               = 50000
        self.n_channels                = 1
        self.default_redis_timeout_ms  = 1
        self.xread_count               = 10

    def initialise_audio_player(self):  
        
        # Initialise pyaudio
        self.aud_p = pyaudio.PyAudio()

        # Print list of available audio devices
        for i in range(self.aud_p.get_device_count()):
            dev = self.aud_p.get_device_info_by_index(i)
            print((i,dev['name'],dev['maxInputChannels']))

        self.audio_stream = self.aud_p.open(format=pyaudio.paFloat32, 
                                            channels=self.n_channels, 
                                            rate=self.audio_fs, 
                                            output=True,
                                            frames_per_buffer=self.chunk_size * self.audio_buffer_scaler,
                                            )
    
    def close_audio_player(self):
        # Close pyAudio player
        self.audio_stream.stop_stream()
        self.audio_stream.close()
        self.aud_p.terminate()

    def work(self):
        # the writable audio buffer size
        audio_buffer_free = self.audio_stream.get_write_available()

        # get the task state [-1=INITIALIZING, 0=START, 1=GO, 3=END, 4=PAUSED]
        try:
            task_state_new = int(self.r.get('task_state_current').decode())
        except:
            task_state_new = self.task_state_default

        if task_state_new != 1:
            if self.task_state != task_state_new:
                print('########## Play audio ended') 
            self.task_state = task_state_new
            self.audio_stream.write(np.zeros(audio_buffer_free, np.float32).tobytes())
        else: # newTaskState == 1
            if self.task_state != task_state_new: #if Go period has started and previous state was not go period
                # New trial began, reset last seen entry
                self.last_entry_seen = "$"
                self.redis_timeout_ms = self.default_redis_timeout_ms
                self.task_state = task_state_new
                print('#########  Play audio started')

            if self.redis_timeout_ms:
                self.audio_stream.write(np.zeros(audio_buffer_free, np.float32).tobytes())

            # get data from feature extraction stream, then parse it, only block for the first read
            try:
                xread_receive = self.r.xread({self.input_stream: self.last_entry_seen},
                                            block=self.redis_timeout_ms,
                                            count=self.xread_count)
            except:
                if self.redis_connected:
                    logging.warning('Lost connection to remote Redis instance.')
                self.redis_connected = False
                return
            else :
                if not self.redis_connected:
                    logging.info('Redis connection established')
                self.redis_connected = True
            
            # only run this if we have data, read 1 ms sample at a time and fill the buffer
            if len(xread_receive) > 0:
                self.redis_timeout_ms = None
                # --------------- Read input stream ------------------------------------
                for entry_id, entry_data in xread_receive[0][1]:
                    self.last_entry_seen = entry_id
                    audio_samples = np.frombuffer(entry_data[b'audio'], np.float32)
                
                audio_samples = audio_samples/self.norm_factor
                np.clip(audio_samples, -1, 1, audio_samples)
                audio_samples = np.ascontiguousarray(audio_samples)
                self.audio_stream.write(audio_samples.tobytes()) # not sure if the buffer behaves like a queue, if it does, this should work
                # self.audio_stream.write(audio_samples[-audio_buffer_free:].tobytes()) # otherwise use this        

    def cleanup(self):
        self.close_audio_player()

if __name__ == "__main__":
    node = AudioPlayer()
    node.run()
