/* quartz_inference.h — ML inference + PUF attestation API */
#ifndef QUARTZ_INFERENCE_H
#define QUARTZ_INFERENCE_H

#include <stdint.h>

#define QZ_ML_INPUT_SIZE  784   /* 28×28 pixels, int8 */
#define QZ_ML_OUTPUT_SIZE 10    /* digits 0-9 */

/* Initialize model hash (call once at startup) */
void quartz_init_model(void);

/* Run inference: 784-byte input → 10 logits → returns predicted digit (0-9) */
int quartz_infer(const int8_t input[QZ_ML_INPUT_SIZE], int32_t logits[QZ_ML_OUTPUT_SIZE]);

/* Compute PUF attestation: SHA256(puf_key || input_hash || output_hash || model_hash) */
void quartz_compute_attestation(const uint8_t input_hash[32],
                                const uint8_t output_hash[32],
                                uint8_t attestation[32]);

/* Get the model hash (SHA256 of all weights) */
void quartz_get_model_hash(uint8_t hash[32]);

#endif /* QUARTZ_INFERENCE_H */