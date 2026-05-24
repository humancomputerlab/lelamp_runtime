#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define LED_COUNT 64
#define MATRIX_W 8
#define MATRIX_H 8
#define DEVICE_PATH "/dev/leds0"
#define PIXEL_BYTES 4

static const char *HEART[MATRIX_H] = {
    "00100100",
    "01111110",
    "11111111",
    "11111111",
    "11111111",
    "01111110",
    "00111100",
    "00011000",
};

static void die(const char *message) {
    fprintf(stderr, "%s: %s\n", message, strerror(errno));
    exit(1);
}

static void clear_pixels(uint8_t *pixels) {
    memset(pixels, 0, LED_COUNT * PIXEL_BYTES);
}

static int xy_to_index_serpentine(int x, int y) {
    if (y % 2 == 0) {
        return y * MATRIX_W + x;
    }
    return y * MATRIX_W + (MATRIX_W - 1 - x);
}

static void set_pixel(uint8_t *pixels, int index, uint8_t red, uint8_t green, uint8_t blue) {
    if (index < 0 || index >= LED_COUNT) {
        return;
    }
    pixels[index * PIXEL_BYTES + 0] = red;
    pixels[index * PIXEL_BYTES + 1] = green;
    pixels[index * PIXEL_BYTES + 2] = blue;
    pixels[index * PIXEL_BYTES + 3] = 0;
}

static void write_pixels(const uint8_t *pixels) {
    int fd = open(DEVICE_PATH, O_WRONLY);
    if (fd < 0) {
        die("open /dev/leds0 failed");
    }

    ssize_t written = write(fd, pixels, LED_COUNT * PIXEL_BYTES);
    close(fd);

    if (written != LED_COUNT * PIXEL_BYTES) {
        fprintf(stderr, "short write: expected %d bytes, got %zd\n", LED_COUNT * PIXEL_BYTES, written);
        exit(1);
    }
}

static void sleep_ms(int milliseconds) {
    struct timespec ts;
    ts.tv_sec = milliseconds / 1000;
    ts.tv_nsec = (long)(milliseconds % 1000) * 1000000L;
    nanosleep(&ts, NULL);
}

static void show_solid(uint8_t red, uint8_t green, uint8_t blue) {
    uint8_t pixels[LED_COUNT * PIXEL_BYTES];
    clear_pixels(pixels);

    for (int i = 0; i < LED_COUNT; ++i) {
        set_pixel(pixels, i, red, green, blue);
    }

    write_pixels(pixels);
}

static void show_heart(void) {
    uint8_t pixels[LED_COUNT * PIXEL_BYTES];
    clear_pixels(pixels);

    for (int y = 0; y < MATRIX_H; ++y) {
        for (int x = 0; x < MATRIX_W; ++x) {
            if (HEART[y][x] == '1') {
                set_pixel(pixels, xy_to_index_serpentine(x, y), 255, 0, 0);
            }
        }
    }

    write_pixels(pixels);
}

static void pixel_scan(int delay_ms) {
    uint8_t pixels[LED_COUNT * PIXEL_BYTES];

    for (int i = 0; i < LED_COUNT; ++i) {
        clear_pixels(pixels);
        set_pixel(pixels, i, 255, 255, 255);
        write_pixels(pixels);
        printf("pixel %d\n", i);
        fflush(stdout);
        sleep_ms(delay_ms);
    }

    clear_pixels(pixels);
    write_pixels(pixels);
}

static void print_usage(const char *program) {
    fprintf(stderr, "usage:\n");
    fprintf(stderr, "  %s solid <r> <g> <b>\n", program);
    fprintf(stderr, "  %s heart\n", program);
    fprintf(stderr, "  %s pixel-scan [delay_ms]\n", program);
    fprintf(stderr, "  %s off\n", program);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        print_usage(argv[0]);
        return 1;
    }

    if (strcmp(argv[1], "solid") == 0) {
        if (argc != 5) {
            print_usage(argv[0]);
            return 1;
        }
        show_solid((uint8_t)atoi(argv[2]), (uint8_t)atoi(argv[3]), (uint8_t)atoi(argv[4]));
        return 0;
    }

    if (strcmp(argv[1], "heart") == 0) {
        show_heart();
        return 0;
    }

    if (strcmp(argv[1], "pixel-scan") == 0) {
        int delay_ms = 350;
        if (argc >= 3) {
            delay_ms = atoi(argv[2]);
        }
        pixel_scan(delay_ms);
        return 0;
    }

    if (strcmp(argv[1], "off") == 0) {
        show_solid(0, 0, 0);
        return 0;
    }

    print_usage(argv[0]);
    return 1;
}
