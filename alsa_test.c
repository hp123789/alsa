#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <hiredis/hiredis.h>
#include <alsa/asoundlib.h>
#include <unistd.h>

#define FS 30000
#define CHUNK_SIZE (FS * 0.01)

void setVolume(long volume) {
    long min, max;
    snd_mixer_t *handle;
    snd_mixer_selem_id_t *sid;
    const char *card = "default";
    const char *selem_name = "Master";

    snd_mixer_open(&handle, 0);
    snd_mixer_attach(handle, card);
    snd_mixer_selem_register(handle, NULL, NULL);
    snd_mixer_load(handle);

    snd_mixer_selem_id_alloca(&sid);
    snd_mixer_selem_id_set_index(sid, 0);
    snd_mixer_selem_id_set_name(sid, selem_name);
    snd_mixer_elem_t* elem = snd_mixer_find_selem(handle, sid);

    snd_mixer_selem_get_playback_volume_range(elem, &min, &max);
    snd_mixer_selem_set_playback_volume_all(elem, volume * max / 100);

    snd_mixer_close(handle);
}

int main (int argc, char **argv) {
    redisReply *reply;
    redisContext *c;
    redisReply *data;
    float *pcmData;

    nice(-20);

    snd_pcm_t *handle;
    int err = snd_pcm_open(&handle, "default", SND_PCM_STREAM_PLAYBACK, 0);
    snd_pcm_set_params(handle, SND_PCM_FORMAT_FLOAT_LE, SND_PCM_ACCESS_RW_INTERLEAVED, 1, FS, 0, 12000);

    setVolume(10);

    c = redisConnect("192.168.150.2", 6379);
    if (c->err) {
        printf("error: %s\n", c->errstr);
        return 1;
    }

    /* PINGs */
    // reply = redisCommand(c,"PING %s", "Hello World");
    while (1) {
        reply = redisCommand(c, "XREAD BLOCK 0 COUNT 1 STREAMS audio $");
        size_t dataReply = reply->elements;
        if (dataReply == 1) {
            redisReply *data = reply->element[0]->element[1]->element[0]->element[1]->element[3];
            size_t dataSize = data->len;
            pcmData = (float *)data->str;
            int nFloats = dataSize / sizeof(float);
            // for (int j=0;j<sizeof(pcmData)/sizeof(*pcmData);j++) {
            //     pcmData[j] = (pcmData[j]/(float)50000);
            //     if (pcmData[j] > 1) {
            //         pcmData[j] = (float)1;
            //     }
            //     if (pcmData[j] < -1) {
            //         pcmData[j] = (float)-1;
            //     }
            //     // printf("%f,", pcmData[j]);
            // }
            err = snd_pcm_writei(handle, pcmData, nFloats);
            if (err > 0) {
                printf("Write %c frames\n", err);
            } else {
                printf("Error write %s\n", snd_strerror(err));
                snd_pcm_recover(handle, err, 0);
                continue;
            }
            // printf("\n");
            freeReplyObject(reply);
        }
    }

    redisFree(c);
    return 0;
}
