#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <hiredis/hiredis.h>

int main (int argc, char **argv) {
    redisReply *reply;
    redisContext *c;
    redisReply *data;
    float *pcmData;

    c = redisConnect("192.168.150.2", 6379);
    if (c->err) {
        printf("error: %s\n", c->errstr);
        return 1;
    }

    /* PINGs */
    // reply = redisCommand(c,"PING %s", "Hello World");
    while (1) {
        reply = redisCommand(c, "XREAD BLOCK 1 COUNT 10 STREAMS audio $");
        size_t dataReply = reply->elements;
        if (dataReply == 1) {
            redisReply *data = reply->element[0]->element[1]->element[0]->element[1]->element[3];
            size_t dataSize = data->len;
            pcmData = (float *)data->str;
            for (int j=0;j<sizeof(pcmData)/sizeof(*pcmData);j++) {
                 pcmData[j] = (pcmData[j]/(float)50000);
                 if (pcmData[j] > 1) {
                     pcmData[j] = (float)1;
                 }
                 if (pcmData[j] < -1) {
                     pcmData[j] = (float)-1;
                 }
                printf("%f,", pcmData[j]);
            }
            printf("\n");
            freeReplyObject(reply);
        }
    }

    redisFree(c);
    return 0;
}
