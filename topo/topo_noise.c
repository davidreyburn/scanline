/* topo_noise.c — Fast noise + atlas render for topo_renderer.py
 *
 * Compile on the Pi:
 *   gcc -O3 -march=native -shared -fPIC -o topo_noise.so topo_noise.c -lm -lpthread
 *
 * Exports:
 *   compute_noise_grid      — warped fBm on a half-res grid (multi-threaded)
 *   bilinear_upsample       — upsample half-res float grid to full resolution
 *   compute_slots           — elevation → glyph/color slot arrays
 *   render_chars            — scatter 16-bit atlas tiles into the back buffer
 *   render_chars_32         — scatter 32-bit atlas tiles into the back buffer
 */

#include <math.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>

/* ---------- Noise --------------------------------------------------------- */

#define HMAX_F 2147483647.0f
#define N_THREADS 4

static inline uint32_t hash2(int32_t ix, int32_t iy)
{
    uint32_t h = ((uint32_t)ix * 1619u + (uint32_t)iy * 31337u) & 0x7FFFFFFFu;
    h = (((h >> 16u) ^ h) * 0x45D9F3Bu) & 0xFFFFFFFFu;
    h = (((h >> 16u) ^ h) * 0x45D9F3Bu) & 0xFFFFFFFFu;
    return ((h >> 16u) ^ h) & 0x7FFFFFFFu;
}

static inline float value_noise(float x, float y)
{
    int32_t ix = (int32_t)floorf(x);
    int32_t iy = (int32_t)floorf(y);
    float fx = x - (float)ix;
    float fy = y - (float)iy;
    fx = fx*fx*fx*(fx*(fx*6.0f - 15.0f) + 10.0f);
    fy = fy*fy*fy*(fy*(fy*6.0f - 15.0f) + 10.0f);
    float v00 = (float)hash2(ix,   iy  ) / HMAX_F;
    float v10 = (float)hash2(ix+1, iy  ) / HMAX_F;
    float v01 = (float)hash2(ix,   iy+1) / HMAX_F;
    float v11 = (float)hash2(ix+1, iy+1) / HMAX_F;
    float a = v00 + fx*(v10 - v00);
    float b = v01 + fx*(v11 - v01);
    return a + fy*(b - a);
}

static float fbm(float x, float y, int octaves)
{
    float v = 0.0f, amp = 0.5f, freq = 1.0f, maxv = 0.0f;
    for (int i = 0; i < octaves; i++) {
        v    += value_noise(x * freq, y * freq) * amp;
        maxv += amp;
        amp  *= 0.5f;
        freq *= 2.0f;
    }
    return v / maxv;
}

typedef struct {
    float *out;
    int    W, H_start, H_end;
    float  dx, dy, nx, ny;
} NoiseArgs;

static void *noise_thread_fn(void *arg)
{
    NoiseArgs *a = (NoiseArgs *)arg;
    for (int row = a->H_start; row < a->H_end; row++) {
        float y = (float)row * a->ny;
        for (int col = 0; col < a->W; col++) {
            float x    = (float)col * a->nx;
            float qx   = fbm(x + a->dx,   y + 0.3f + a->dy*0.4f, 2);
            float qy   = fbm(x + 1.7f,    y + 9.2f + a->dy,      2);
            a->out[row * a->W + col] = fbm(x + 2.2f*qx + 1.3f + a->dx*0.6f,
                                           y + 2.2f*qy + 9.2f + a->dy*0.5f, 3);
        }
    }
    return NULL;
}

void compute_noise_grid(float *out, int W, int H,
                        float dx, float dy, float nx, float ny)
{
    pthread_t  threads[N_THREADS];
    NoiseArgs  args[N_THREADS];
    int chunk = (H + N_THREADS - 1) / N_THREADS;

    for (int i = 0; i < N_THREADS; i++) {
        int h0 = i * chunk;
        int h1 = h0 + chunk < H ? h0 + chunk : H;
        args[i] = (NoiseArgs){ out, W, h0, h1, dx, dy, nx, ny };
        pthread_create(&threads[i], NULL, noise_thread_fn, &args[i]);
    }
    for (int i = 0; i < N_THREADS; i++)
        pthread_join(threads[i], NULL);
}

/* ---------- Bilinear upsample --------------------------------------------- */

void bilinear_upsample(const float *small, int W2, int H2,
                       float *out, int W, int H)
{
    for (int row = 0; row < H; row++) {
        float sy = (float)(H2 - 1) * row / (float)(H - 1);
        int   iy = (int)sy;
        if (iy > H2 - 2) iy = H2 - 2;
        float fy = sy - (float)iy;

        for (int col = 0; col < W; col++) {
            float sx = (float)(W2 - 1) * col / (float)(W - 1);
            int   ix = (int)sx;
            if (ix > W2 - 2) ix = W2 - 2;
            float fx = sx - (float)ix;

            float v00 = small[ iy      * W2 +  ix     ];
            float v10 = small[ iy      * W2 + (ix + 1)];
            float v01 = small[(iy + 1) * W2 +  ix     ];
            float v11 = small[(iy + 1) * W2 + (ix + 1)];

            out[row * W + col] = v00*(1.0f-fy)*(1.0f-fx)
                               + v10*(1.0f-fy)*fx
                               + v01*fy*(1.0f-fx)
                               + v11*fy*fx;
        }
    }
}

/* ---------- Slot computation ---------------------------------------------- */

void compute_slots(
    const float *elev,
    int ROWS, int COLS,
    const float *thresholds, int N_BANDS,
    int fill_len,
    int contour_levels,
    int32_t *glyph_out,
    int32_t *color_out)
{
    for (int row = 0; row < ROWS; row++) {
        for (int col = 0; col < COLS; col++) {
            float e = elev[row * COLS + col];

            int cs = 0;
            for (int b = 0; b < N_BANDS; b++) {
                if (thresholds[b] <= e) cs++;
                else break;
            }
            if (cs >= N_BANDS) cs = N_BANDS - 1;

            float e_clamped = e < 0.999f ? e : 0.999f;
            int gs = (int)(e_clamped * (float)fill_len);
            if (gs >= fill_len) gs = fill_len - 1;

            color_out[row * COLS + col] = cs;
            glyph_out[row * COLS + col] = gs;
        }
    }

    if (contour_levels > 0) {
        for (int row = 0; row < ROWS; row++) {
            for (int col = 0; col < COLS; col++) {
                float e       = elev[row * COLS + col];
                float e_right = (col < COLS - 1) ? elev[row * COLS + col + 1] : e;
                float e_down  = (row < ROWS - 1) ? elev[(row + 1) * COLS + col] : e;

                int bg = (int)(e       * (float)contour_levels);
                int br = (int)(e_right * (float)contour_levels);
                int bd = (int)(e_down  * (float)contour_levels);

                if (bg != br || bg != bd) {
                    int has_h = fabsf(e - e_right) > 0.001f ? 1 : 0;
                    int has_v = fabsf(e - e_down)  > 0.001f ? 1 : 0;
                    color_out[row * COLS + col] = N_BANDS;
                    glyph_out[row * COLS + col] = fill_len + has_h + has_v * 2;
                }
            }
        }
    }
}

/* ---------- Render -------------------------------------------------------- */

void render_chars(
    uint16_t       *back_buf,
    const uint16_t *atlas,
    const int32_t  *flat_idx,
    int ROWS,   int COLS,
    int CHAR_H, int CHAR_W,
    int N_atlas, int SCR_W)
{
    size_t atlas_row_stride = (size_t)N_atlas * CHAR_W;
    size_t char_bytes       = (size_t)CHAR_W  * sizeof(uint16_t);

    for (int row = 0; row < ROWS; row++) {
        const int32_t *idx_row = flat_idx + (size_t)row * COLS;
        for (int py = 0; py < CHAR_H; py++) {
            uint16_t       *dst  = back_buf + (size_t)(row*CHAR_H + py) * SCR_W;
            const uint16_t *a_py = atlas   + (size_t)py * atlas_row_stride;
            for (int col = 0; col < COLS; col++) {
                memcpy(dst  + (size_t)col * CHAR_W,
                       a_py + (size_t)idx_row[col] * CHAR_W,
                       char_bytes);
            }
        }
    }
}

void render_chars_32(
    uint32_t       *back_buf,
    const uint32_t *atlas,
    const int32_t  *flat_idx,
    int ROWS,   int COLS,
    int CHAR_H, int CHAR_W,
    int N_atlas, int SCR_W)
{
    size_t atlas_row_stride = (size_t)N_atlas * CHAR_W;
    size_t char_bytes       = (size_t)CHAR_W  * sizeof(uint32_t);

    for (int row = 0; row < ROWS; row++) {
        const int32_t *idx_row = flat_idx + (size_t)row * COLS;
        for (int py = 0; py < CHAR_H; py++) {
            uint32_t       *dst  = back_buf + (size_t)(row*CHAR_H + py) * SCR_W;
            const uint32_t *a_py = atlas   + (size_t)py * atlas_row_stride;
            for (int col = 0; col < COLS; col++) {
                memcpy(dst  + (size_t)col * CHAR_W,
                       a_py + (size_t)idx_row[col] * CHAR_W,
                       char_bytes);
            }
        }
    }
}

/* render_chars_32_cm — column-major output for pygame surfarray (W,H) layout.
 * Loop order: (col, char_col) outer → writes to each screen column are
 * sequential; atlas reads are strided but atlas (~92 KB) stays hot in L2. */
void render_chars_32_cm(
    uint32_t       *cm_buf,   /* (W, H) column-major: cm_buf[x * SCR_H + y] */
    const uint32_t *atlas,    /* (CHAR_H, N_atlas, CHAR_W) row-major */
    const int32_t  *flat_idx, /* (ROWS * COLS)             row-major */
    int ROWS, int COLS,
    int CHAR_H, int CHAR_W,
    int N_atlas, int SCR_H)
{
    size_t atlas_row_stride = (size_t)N_atlas * CHAR_W;

    for (int col = 0; col < COLS; col++) {
        for (int char_col = 0; char_col < CHAR_W; char_col++) {
            int       x       = col * CHAR_W + char_col;
            uint32_t *col_dst = cm_buf + (size_t)x * SCR_H;
            for (int row = 0; row < ROWS; row++) {
                int             idx     = flat_idx[(size_t)row * COLS + col];
                uint32_t       *row_dst = col_dst  + (size_t)row * CHAR_H;
                const uint32_t *src0    = atlas
                                         + (size_t)idx * CHAR_W + char_col;
                for (int py = 0; py < CHAR_H; py++)
                    row_dst[py] = src0[(size_t)py * atlas_row_stride];
            }
        }
    }
}
