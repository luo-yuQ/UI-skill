ToAPIs gpt-image-2 Reference-Image Size Contract v0.1

For requested portrait size 1024x1536:

text-to-image:
reference_count = 0
actual_output = 1024x1536

image-to-image:
reference_count >= 1
actual_output = 832x1248

Observed scale:
832 / 1024 = 0.8125
1248 / 1536 = 0.8125

Therefore:
requested_size must not be interpreted as guaranteed output pixel size
when reference images are supplied.

Runtime code must inspect the actual returned image dimensions.